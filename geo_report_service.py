"""地政学インテリジェンス基盤 Phase4: Internal Report Upload → Knowledge化。

既存のDocument ingestion（geo_documents_store.save_document、PDF/DOCX/TXT抽出）・Chunk化
（geo_claim_service.chunk_document）・Claim機構（geo_claim_service.create_claim/link_evidence/
link_claim_to_issue）をそのまま再利用する。新しい文書基盤・新しいReport専用検索エンジンは
作らない。geo_reportsはgeo_documentsの1:1メタデータ拡張のみ。

パイプライン:
    Upload（ファイルbytes） → Document（geo_documents_store.save_document） → geo_reports行
    → Chunk（geo_claim_service.chunk_document） → Claim（各Chunkの実際の引用として作成、
      information_class='internal_expert_assessment'or'external_assessment'。要約を捏造せず
      quoted_excerptをそのまま使う） → Evidence Link → Issue Link（geo_claim_issues）
    → 次回Geo Intelligence Requestのretrieve_knowledgeから即座に検索対象になる
      （手作業での別DB転記は発生しない）。

Internal Expert Assessment/Internal Expert Reasoningとの関係: Reportから生成したClaimは
information_class='internal_expert_assessment'（report_kind='internal_report'の場合）を
付与するのみで、Knowledge Item化（review_knowledge_item相当の承認）は行わない。「Reasoning
Rule/Precedent等の再利用可能な思考法」への昇格は既存のInternal Expert Learning Layer
（Expert Review Loop→Delta Extraction→Promote）の役割のままとし、素のingest_report/
create_report_versionでは代替しない（すべてを同じ「正解Knowledge」として混ぜないため）。

地政学アプリClooseout（Phase3〜5）で以下を追加した:
- Phase3: Report Update/Versioning（version/supersedes_id/report_identity_id、
  create_report_version/get_latest_report_version/list_report_versions/diff_report_versions）。
- Phase4/5: start_report_review_and_versionが、上記Versioningと既存Expert Review Loop
  （geo_expert_review_service、report_id対応済み）を明示的に接続する。この関数を通した場合のみ、
  Reportのレビュー差分がgeo_knowledge_items（knowledge_type='report_finding'）へ昇格され得る
  （Human Promoteが必須、自動Active化はしない）。素のReport Upload/Version作成だけでは
  引き続きKnowledge Item化しない（上記の原則を維持）。
"""
import difflib
import uuid
from pathlib import Path

import requests

import geo_audit
import geo_claim_service
import geo_connector_base as base
import geo_documents_store
import geo_expert_review_service
import geo_intelligence_service
import geo_knowledge_access_policy
import geo_llm_common
import geo_search_service

MAX_CLAIMS_PER_REPORT = 30

INTERNAL_REPORT_SOURCE_NAME = "Internal Analyst Report"

# 直接登録（Review Flowを経ないRegister）由来の行を示す既定値（Phase 2D）。
REGISTRATION_SOURCE_DIRECT = "direct_registration"

APPROVED_REPORT_KNOWLEDGE_TYPE = "approved_report"


class GeoReportError(Exception):
    """Report Upload/Ingestion処理の失敗。"""


class ReportVersionConflictError(GeoReportError):
    """latest version以外から新Versionを作成しようとした場合（version番号の分岐・重複防止）。"""


class ReportVersionNoChangeError(GeoReportError):
    """document内容・metadataともに変更が無いVersion作成を拒否する場合。"""


# ---- Phase 2D: レポート登録（Direct Report Registration）AI Metadata Extraction -----
# 既存geo_llm_common.call_llm_structured（geo_ios_review_import_service.pyと同じ汎用
# 構造化抽出ヘルパー）を再利用する。新しいAI基盤・新しいLLM呼び出し経路は作らない。

_METADATA_SCHEMA_NAME = "geo_report_registration_metadata_v1"
_METADATA_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": ["string", "null"]},
        "report_date": {"type": ["string", "null"]},
        "region": {"type": "array", "items": {"type": "string"}},
        "topics": {"type": "array", "items": {"type": "string"}},
        "key_actors": {"type": "array", "items": {"type": "string"}},
        "author": {"type": ["string", "null"]},
        "organization": {"type": ["string", "null"]},
        "report_type": {"type": ["string", "null"]},
        "language": {"type": ["string", "null"]},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": ["string", "null"]},
        "business_context": {"type": ["string", "null"]},
        "not_explicit_fields": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "report_date", "region", "topics", "key_actors", "author",
                 "organization", "report_type", "language", "keywords", "summary",
                 "business_context", "not_explicit_fields"],
    "additionalProperties": False,
}

_METADATA_SYSTEM_PROMPT = """あなたは、アップロードされたレポート本文からメタデータを
抽出するアシスタントです。新しい分析・評価・要約以上の解釈は行わないでください。

厳守事項（絶対条件）:
- 本文に明示されている内容のみを抽出してください。一般知識・外部情報で補完・推測しないで
  ください。特にauthor（著者）・report_date（作成日/発行日）・organization（組織・出典）は、
  本文に明記が無い限り絶対に推測しないでください。
- title: 本文にタイトルが明記されていればそれを使う。無ければ内容から妥当な短い題名を作る。
- report_date: 本文に明記された作成日・発行日・対象日をISO 8601形式（YYYY-MM-DD）で。
  不明ならnull。
- region: 本文が扱う国・地域名のリスト。
- topics: 本文が扱うテーマ・トピックのリスト。
- key_actors: 本文に登場する主要な国・組織・人物名のリスト。
- author/organization/report_type/language: 本文に明記されている場合のみ埋める。
- keywords: 本文由来の自由なキーワード（5-10個程度）。
- summary: 本文の要約（3-5文程度）。
- business_context: 当社（サントリー等の企業）との潜在的接点が本文から読み取れる場合のみ、
  1-2文で。読み取れなければnull。
- 該当する記述が本文に無い項目は、null・空配列のままにし、not_explicit_fieldsにそのfield名を
  追加してください（無理に埋めない）。"""


def extract_report_metadata_with_ai(azure_client, model, *, document_text: str) -> dict:
    """Phase 2D §AI Metadata Extraction。DBへは何も書き込まない（呼び出し側がUser
    Confirmation/Edit後にingest_report/create_report_versionへ渡す）。"""
    # 巨大な本文でもLLM呼び出し自体は既存パターン（他のextract系関数）と同じ単発呼び出し
    # のままにする（新しいchunk分割ロジックは作らない、長すぎる場合は既存のトークン制限に
    # 委ねる——geo_llm_common側の既存挙動のまま）。
    user_prompt = f"# レポート全文\n{document_text}"
    result = geo_llm_common.call_llm_structured(
        azure_client, model, _METADATA_SYSTEM_PROMPT, user_prompt,
        _METADATA_SCHEMA, _METADATA_SCHEMA_NAME, max_attempts=2)
    return result["data"]


def analyze_report_upload(azure_client, model, *, filename: str, mime_type: str,
                           raw_bytes: bytes) -> dict:
    """Phase 2D §ファイル解析→AI Metadata Extraction。既存geo_documents_store.extract_text
    （PDF/Word/HTML/text対応の既存共通処理、新しいparserは追加しない）でテキスト抽出した上で
    AI抽出を行う。DBへは何も書き込まない（analyze専用、Registerは別途ingest_reportを呼ぶ）。
    HTMLのみ抽出できるextract_text自身のtitle/language/published_at（trafilatura由来の
    非AI・決定的な値）があれば、AIがnullを返した項目のみ補完に使う（AIの推測より確定的な
    値を優先する）。"""
    extracted = geo_documents_store.extract_text(raw_bytes, filename, mime_type)
    if not extracted.get("ok"):
        raise GeoReportError(f"文書からテキストを抽出できませんでした: {extracted.get('error')}")

    metadata = extract_report_metadata_with_ai(azure_client, model, document_text=extracted["text"])
    if not metadata.get("title") and extracted.get("title"):
        metadata["title"] = extracted["title"]
    if not metadata.get("language") and extracted.get("language"):
        metadata["language"] = extracted["language"]
    if not metadata.get("report_date") and extracted.get("published_at"):
        metadata["report_date"] = extracted["published_at"]
    return {"metadata": metadata, "extracted_text_length": len(extracted["text"])}


def derive_report_review_status(client, reports: list) -> None:
    """Phase 2D: 既存のReview/Approvalデータからreview_statusを導出し、各report dictへ
    'review_status'キーを追加する（in-place、新しいstatus columnは増やさない）。

    導出できるのは以下3値のみ:
    - "approved": Route B publish_external_approved_report（geo_ios_knowledge_publication_
      service.py）等により、knowledge_type='approved_report'のActive Knowledge Itemが
      source_report_idから存在する。
    - "in_review": geo_expert_review_sessions（Report Review Loop、report_id列）に
      completed_atがまだ無い行が存在する。
    - "uploaded_only": 上記のいずれでもない（Review Flowを経ずに登録されたまま）。

    "revision_required"/"stopped"/"rejected"は、geo_reports起点の既存データには対応する
    概念が無いため（geo_expert_review_sessionsはdelta_extraction_status pending/completed/
    failedのみで、承認/却下の概念を持たない）、ここでは導出しない——推測でstatusを
    捏造しない。Review Flow（IOS）起点のReportはgeo_ios_review_cycles/geo_ios_approval_
    eventsから別途導出する（geo_ios_workflow_board_service.derive_current_step等）。"""
    report_ids = sorted({r["report_id"] for r in reports if r.get("report_id")})
    if not report_ids:
        return
    id_list = ",".join(report_ids)

    active_knowledge = client.select("geo_knowledge_items", {
        "select": "source_report_id", "knowledge_type": f"eq.{APPROVED_REPORT_KNOWLEDGE_TYPE}",
        "status": "eq.active", "source_report_id": f"in.({id_list})"})
    approved_report_ids = {k["source_report_id"] for k in active_knowledge if k.get("source_report_id")}

    open_reviews = client.select("geo_expert_review_sessions", {
        "select": "report_id", "report_id": f"in.({id_list})", "completed_at": "is.null"})
    in_review_report_ids = {r["report_id"] for r in open_reviews if r.get("report_id")}

    for r in reports:
        rid = r.get("report_id")
        if rid in approved_report_ids:
            r["review_status"] = "approved"
        elif rid in in_review_report_ids:
            r["review_status"] = "in_review"
        else:
            r["review_status"] = "uploaded_only"


def _build_search_text_source(*, title: str = None, author: str = None, owner_department: str = None,
                               report_type: str = None, summary: str = None, business_context: str = None,
                               topics: list = None, country_region: list = None, key_actors: list = None,
                               keywords: list = None) -> str:
    """Phase 2F-1: geo_reports.search_text_source（search_vectorのgenerated column元）を
    組み立てる。geo_documents.title等、geo_reports自テーブルに無い値も呼び出し側
    （ingest_report/create_report_version）が既に持っているためここで結合する
    （generated columnは他テーブルを参照できないため、Python側で一度実体化する）。"""
    parts = [title, author, owner_department, report_type, summary, business_context]
    for values in (topics, country_region, key_actors, keywords):
        parts.extend(values or [])
    return " ".join(p for p in parts if p)


def _get_or_create_report_source(client, *, theme_id: str, actor_id: str) -> dict:
    """アップロードされたReportがShareする、Theme単位のSource（Steward操作としての登録、
    内容の正誤判定ではない）。geo_source_registry.theme_idはNOT NULLのため、Sourceは
    Theme単位で1つ（既存geo_hormuz_pilot_seed.py等と同じ、Source登録の既存パターン）。
    既存の'Internal Expert Review'（Expert Review Loop用、trust_tier='E'）とは別に、
    Report Upload由来と識別できるSourceを用意する。"""
    existing = client.select("geo_source_registry", {
        "select": "*", "theme_id": f"eq.{theme_id}", "name": f"eq.{INTERNAL_REPORT_SOURCE_NAME}"})
    if existing:
        source = existing[0]
    else:
        source = client.insert("geo_source_registry", [{
            "theme_id": theme_id, "name": INTERNAL_REPORT_SOURCE_NAME, "url": None, "source_type": "other",
            "trust_tier": "B", "source_owner": "geopolitical_intelligence_team",
            "access_method": "manual", "automation_allowed": False,
        }])[0]
    if source.get("reference_availability") != "available":
        base.set_reference_availability(client, source_id=source["source_id"],
                                         reference_availability="available", actor_id=actor_id)
        source = client.select("geo_source_registry", {
            "select": "*", "source_id": f"eq.{source['source_id']}", "limit": "1"})[0]
    return source


def ingest_report(client, *, filename: str, mime_type: str, raw_bytes: bytes, title: str = "",
                   author: str = None, owner_department: str = None, report_type: str = None,
                   country_region: list = None, topics: list = None,
                   confidentiality: str = "internal", report_kind: str = "internal_report",
                   as_of: str = None, theme_id: str = None, linked_gap_ids: list = None,
                   actor_id: str = "analyst",
                   # Phase 2D（レポート登録）追加項目。既存呼び出し元（Upload Reportダイアログ）
                   # は省略したままでよく、その場合は従来通り空/nullのまま登録される。
                   key_actors: list = None, keywords: list = None, summary: str = None,
                   business_context: str = None, registration_source: str = None,
                   # Phase 2E（Confidentiality & Retrieval Access Control）追加項目。
                   # 3つのUI Label（部内限り/SHD限り/グループ配信可）のいずれかを渡すと、
                   # geo_knowledge_access_policy.resolve_ui_label_to_policyでhuman_visibility/
                   # agent_sharing_policy/allowed_org_units/allowed_domainsへ解決した上で保存
                   # する（生のenum値を呼び出し元から直接渡させない、§4）。省略時はDB既定値
                   # （human_visibility='custom'+allowed_org_units=[]、fail-closed）のまま。
                   confidentiality_label: str = None) -> dict:
    """Report Uploadのエントリポイント。country_region/topicsから対象Issueを解決し（既存
    geo_intelligence_service.resolve_entities_and_issuesを再利用）、Documentとして保存・Chunk化
    したうえで、各ChunkをClaim化してIssueへ紐付ける。theme_id未指定時はcountry_region/topicsの
    解決結果から単一Themeが決まる場合のみ自動補完する（geo_documents.theme_idは必須のため、
    決まらない場合は明示的にエラーとし、推測で埋めない）。"""
    country_region = country_region or []
    access_policy_fields = (
        geo_knowledge_access_policy.resolve_ui_label_to_policy(
            confidentiality_label, owner_department=owner_department)
        if confidentiality_label else None)
    topics = topics or []
    key_actors = key_actors or []
    keywords = keywords or []
    if report_kind not in ("internal_report", "external_report"):
        raise GeoReportError(f"未対応のreport_kindです: {report_kind}")

    resolved = geo_intelligence_service.resolve_entities_and_issues(
        client, {"country_region": country_region, "themes": topics})
    issue_ids = resolved["issue_ids"]

    resolved_theme_id = theme_id or geo_intelligence_service.resolve_single_theme_id(client, issue_ids)
    if not resolved_theme_id:
        raise GeoReportError(
            "theme_idを自動解決できませんでした（country_region/topicsが複数Themeにまたがる、"
            "または既存Entityへ解決できませんでした）。theme_idを明示的に指定してください。")

    themes = client.select("geo_themes", {"select": "code", "theme_id": f"eq.{resolved_theme_id}", "limit": "1"})
    if not themes:
        raise GeoReportError(f"theme_idが見つかりません: {resolved_theme_id}")
    theme_code = themes[0]["code"]

    source = _get_or_create_report_source(client, theme_id=resolved_theme_id, actor_id=actor_id)

    doc_result = geo_documents_store.save_document(
        client, theme_id=resolved_theme_id, theme_code=theme_code, source_id=source["source_id"],
        filename=filename, mime_type=mime_type, raw_bytes=raw_bytes,
        title=title, published_at=as_of, is_mock=False,
    )
    document = doc_result["document"]

    # Report Versioning（Closeout Phase3）: document_idを共有する複数Version行が存在し得るため、
    # 冪等性チェックはlatest版（version.desc）を採用する。
    existing_reports = client.select("geo_reports", {
        "select": "*", "document_id": f"eq.{document['document_id']}",
        "order": "version.desc", "limit": "1"})
    if existing_reports:
        report = existing_reports[0]
    else:
        # 新規root Report（v1）: report_idをPython側で先に生成し、report_identity_id/version/
        # supersedes_idを最初から自己完結させる（DBのgen_random_uuid()デフォルトに任せない）。
        new_report_id = str(uuid.uuid4())
        row = {
            "report_id": new_report_id, "document_id": document["document_id"], "author": author,
            "owner_department": owner_department, "report_type": report_type,
            "country_region": country_region, "topics": topics, "confidentiality": confidentiality,
            "report_kind": report_kind, "uploaded_by": actor_id,
            "version": 1, "supersedes_id": None, "report_identity_id": new_report_id,
            "key_actors": key_actors, "keywords": keywords, "summary": summary,
            "business_context": business_context, "registration_source": registration_source,
            "search_text_source": _build_search_text_source(
                title=title, author=author, owner_department=owner_department, report_type=report_type,
                summary=summary, business_context=business_context, topics=topics,
                country_region=country_region, key_actors=key_actors, keywords=keywords),
        }
        if access_policy_fields:
            row.update(access_policy_fields)
        report = client.insert("geo_reports", [row])[0]

    claims_created = []
    if doc_result["is_new"] or not existing_reports:
        chunks = geo_claim_service.chunk_document(client, document["document_id"])
        information_class = ("internal_expert_assessment" if report_kind == "internal_report"
                              else "external_assessment")
        for chunk in chunks[:MAX_CLAIMS_PER_REPORT]:
            existing_claims = client.select("geo_claims", {
                "select": "claim_id", "origin_type": "eq.document", "origin_id": f"eq.{document['document_id']}",
                "claim_text": f"eq.{chunk['quoted_excerpt']}",
            })
            if existing_claims:
                claim = existing_claims[0]
            else:
                claim = geo_claim_service.create_claim(
                    client, claim_text=chunk["quoted_excerpt"], information_class=information_class,
                    origin_type="document", origin_id=document["document_id"], actor_id=actor_id,
                    source_id=source["source_id"])
                geo_claim_service.link_evidence(client, claim_id=claim["claim_id"], evidence_role="supporting",
                                                 actor_id=actor_id, document_chunk_id=chunk["chunk_id"])
            # 地政学アプリCleanup（P1-3）: issue_idごとの個別存在チェックselect（C×I回）を、
            # claimごとに既存リンク済みissue_id集合を1回で取得する形（C回）へ削減する
            # （新規リンクのinsert回数は変更しない）。
            existing_links = client.select("geo_claim_issues", {
                "select": "issue_id", "claim_id": f"eq.{claim['claim_id']}"})
            already_linked_issue_ids = {link["issue_id"] for link in existing_links}
            for issue_id in issue_ids:
                if issue_id not in already_linked_issue_ids:
                    geo_claim_service.link_claim_to_issue(client, claim_id=claim["claim_id"], issue_id=issue_id)
            claims_created.append(claim)
        skipped = max(0, len(chunks) - MAX_CLAIMS_PER_REPORT)
    else:
        skipped = 0

    if linked_gap_ids:
        for gap_id in linked_gap_ids:
            existing_link = client.select("geo_report_gaps", {
                "select": "*", "report_id": f"eq.{report['report_id']}", "gap_id": f"eq.{gap_id}"})
            if not existing_link:
                client.insert("geo_report_gaps", [{"report_id": report["report_id"], "gap_id": gap_id}],
                              prefer="return=minimal")

    geo_audit.log_audit(client, actor=actor_id, action="geo_report_ingest",
                         target_table="geo_reports", target_id=report["report_id"],
                         detail={"document_id": document["document_id"], "claims_created": len(claims_created),
                                 "issue_ids": issue_ids, "linked_gap_ids": linked_gap_ids or []})

    return {
        "report": report, "document": document, "issue_ids": issue_ids,
        "claims_created": len(claims_created), "chunks_skipped": skipped,
    }


def get_report(client, report_id: str) -> dict | None:
    rows = client.select("geo_reports", {"select": "*", "report_id": f"eq.{report_id}", "limit": "1"})
    if not rows:
        return None
    report = rows[0]
    docs = client.select("geo_documents", {"select": "*", "document_id": f"eq.{report['document_id']}", "limit": "1"})
    report["document"] = docs[0] if docs else None
    derive_report_review_status(client, [report])
    _attach_confidentiality_label([report])
    return report


def delete_report(client, *, report_id: str, actor_id: str = "analyst") -> dict:
    """Reportとその原本Document・派生Claim/Evidence・保存済みファイルを削除する。
    geo_case_external_reference（Case Review WorkflowのDraft/Final紐付け、on delete restrict）
    から参照されている場合は、Caseの監査証跡を壊さないため削除自体を拒否する。geo_reports/
    geo_document_chunks/geo_report_gapsはgeo_documents削除のon delete cascadeに任せるが、
    geo_claims（origin_type/origin_idのpolymorphic参照でFK無し）とgeo_evidence（on delete
    restrictのため先に消さないとgeo_documents自体の削除が失敗する）だけは明示的に削除する。"""
    reports = client.select("geo_reports", {"select": "*", "report_id": f"eq.{report_id}", "limit": "1"})
    if not reports:
        raise GeoReportError("指定されたReportが見つかりません")
    report = reports[0]
    document_id = report["document_id"]

    # geo_case_external_reference（sql/2026-08-19_geo_phase2b2_external_reference.sql）は
    # 環境によってはまだ実DBへmigration未適用の場合がある。テーブル自体が存在しない
    # （PostgRESTが404を返す）場合は「参照するCase外部連携が無い」として扱い、削除自体は
    # ブロックしない（migration未適用は本関数の責務外）。
    try:
        blocking = client.select("geo_case_external_reference", {
            "select": "case_id",
            "or": f"(draft_document_id.eq.{document_id},final_document_id.eq.{document_id})",
        })
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            blocking = []
        else:
            raise
    if blocking:
        case_ids = ", ".join(sorted({b["case_id"] for b in blocking}))
        raise GeoReportError(
            f"このレポートはCase（{case_ids}）のレビュー記録に紐づいているため削除できません。")

    claims = client.select("geo_claims", {
        "select": "claim_id", "origin_type": "eq.document", "origin_id": f"eq.{document_id}"})
    for claim in claims:
        client.delete("geo_claims", {"claim_id": f"eq.{claim['claim_id']}"})

    client.delete("geo_evidence", {"document_id": f"eq.{document_id}"})

    documents = client.select("geo_documents", {
        "select": "storage_path", "document_id": f"eq.{document_id}", "limit": "1"})
    storage_path = documents[0].get("storage_path") if documents else None

    client.delete("geo_documents", {"document_id": f"eq.{document_id}"})

    if storage_path:
        try:
            Path(storage_path).unlink(missing_ok=True)
        except OSError:
            pass  # ファイル削除失敗はDB側の削除を巻き戻すほどの実害ではないため握りつぶす

    geo_audit.log_audit(client, actor=actor_id, action="geo_report_delete",
                         target_table="geo_reports", target_id=report_id,
                         detail={"document_id": document_id, "claims_deleted": len(claims)})
    return {"deleted": True, "report_id": report_id, "document_id": document_id}


def _report_version_metadata_fields(report: dict) -> dict:
    """Version間の実質的な変更判定に使うmetadataフィールドのみを取り出す
    （report_id/version/supersedes_id/report_identity_id/uploaded_at等の識別・監査情報は除く）。"""
    return {k: report.get(k) for k in (
        "author", "owner_department", "report_type", "country_region", "topics",
        "confidentiality", "report_kind",
    )}


def get_latest_report_version(client, report_identity_id: str) -> dict | None:
    rows = client.select("geo_reports", {
        "select": "*", "report_identity_id": f"eq.{report_identity_id}",
        "order": "version.desc", "limit": "1",
    })
    return rows[0] if rows else None


def list_report_versions(client, report_identity_id: str) -> list:
    return client.select("geo_reports", {
        "select": "*", "report_identity_id": f"eq.{report_identity_id}", "order": "version.asc",
    })


def create_report_version(client, *, supersedes_report_id: str, filename: str, mime_type: str,
                           raw_bytes: bytes, author: str = None, owner_department: str = None,
                           report_type: str = None, country_region: list = None, topics: list = None,
                           confidentiality: str = None, report_kind: str = None,
                           as_of: str = None, actor_id: str = "analyst") -> dict:
    """既存Reportの新Version（メタデータ修正のみ、または新ファイル差し替え）を作成する。

    - 原則latest versionからのみ作成を許可する（古いversionからの分岐によるversion番号の
      重複・分岐を防ぐ）。非latestから呼ばれた場合はReportVersionConflictErrorを送出する。
    - ingest_reportをそのまま呼ばない。ingest_report自身の「同じdocument_idの行があれば
      既存行を返す」冪等性ショートカットが働くと、新Version行を作るはずが既存行をそのまま
      返してしまうため、geo_documents_store.save_documentを直接呼んで独自にgeo_reportsへ
      新行をINSERTする。
    - 新Versionのdocument内容（content_hash）とmetadata全項目がsupersedes元と完全に同一の場合、
      実質的な変更が無い版作成としてReportVersionNoChangeErrorを送出し、新版行は作らない。

    metadata系引数（author等）を省略（None）した場合はsupersedes元の値をそのまま引き継ぐ
    （部分的なメタデータ修正を許容する）。"""
    superseded_rows = client.select("geo_reports", {
        "select": "*", "report_id": f"eq.{supersedes_report_id}", "limit": "1"})
    if not superseded_rows:
        raise GeoReportError(f"report_idが見つかりません: {supersedes_report_id}")
    superseded = superseded_rows[0]

    latest = get_latest_report_version(client, superseded["report_identity_id"])
    if not latest or latest["report_id"] != supersedes_report_id:
        raise ReportVersionConflictError(
            f"{supersedes_report_id}はlatest versionではありません"
            f"（latestのreport_id={latest['report_id'] if latest else None}）。"
            "新Versionはlatest versionからのみ作成できます。")

    superseded_documents = client.select("geo_documents", {
        "select": "*", "document_id": f"eq.{superseded['document_id']}", "limit": "1"})
    if not superseded_documents:
        raise GeoReportError(f"documentが見つかりません: {superseded['document_id']}")
    superseded_document = superseded_documents[0]

    new_metadata = {
        "author": author if author is not None else superseded.get("author"),
        "owner_department": (owner_department if owner_department is not None
                              else superseded.get("owner_department")),
        "report_type": report_type if report_type is not None else superseded.get("report_type"),
        "country_region": country_region if country_region is not None else superseded.get("country_region"),
        "topics": topics if topics is not None else superseded.get("topics"),
        "confidentiality": confidentiality if confidentiality is not None else superseded.get("confidentiality"),
        "report_kind": report_kind if report_kind is not None else superseded.get("report_kind"),
    }

    theme_id = superseded_document["theme_id"]
    themes = client.select("geo_themes", {"select": "code", "theme_id": f"eq.{theme_id}", "limit": "1"})
    if not themes:
        raise GeoReportError(f"theme_idが見つかりません: {theme_id}")
    theme_code = themes[0]["code"]
    source = _get_or_create_report_source(client, theme_id=theme_id, actor_id=actor_id)

    doc_result = geo_documents_store.save_document(
        client, theme_id=theme_id, theme_code=theme_code, source_id=source["source_id"],
        filename=filename, mime_type=mime_type, raw_bytes=raw_bytes,
        title=superseded_document.get("title") or "", published_at=as_of, is_mock=False,
    )
    new_document = doc_result["document"]

    if (new_document["document_id"] == superseded["document_id"]
            and new_metadata == _report_version_metadata_fields(superseded)):
        raise ReportVersionNoChangeError(
            "document内容・metadataともに変更がないため、新Versionを作成しませんでした。")

    # Phase 2F-1: summary/business_context/key_actors/keywordsはこの関数の引数に無いため
    # （§18、Phase 2Dの新項目にcreate_report_versionは未対応のまま——今回もそこは広げない）、
    # supersedes元からそのまま引き継いで検索対象から消えないようにする。
    new_report = client.insert("geo_reports", [{
        "document_id": new_document["document_id"], **new_metadata, "uploaded_by": actor_id,
        "version": superseded["version"] + 1, "supersedes_id": supersedes_report_id,
        "report_identity_id": superseded["report_identity_id"],
        "search_text_source": _build_search_text_source(
            title=new_document.get("title"), author=new_metadata["author"],
            owner_department=new_metadata["owner_department"], report_type=new_metadata["report_type"],
            summary=superseded.get("summary"), business_context=superseded.get("business_context"),
            topics=new_metadata["topics"], country_region=new_metadata["country_region"],
            key_actors=superseded.get("key_actors"), keywords=superseded.get("keywords")),
    }])[0]

    claims_created = []
    skipped = 0
    if doc_result["is_new"]:
        resolved = geo_intelligence_service.resolve_entities_and_issues(client, {
            "country_region": new_metadata["country_region"] or [], "themes": new_metadata["topics"] or [],
        })
        issue_ids = resolved["issue_ids"]
        chunks = geo_claim_service.chunk_document(client, new_document["document_id"])
        information_class = ("internal_expert_assessment" if new_metadata["report_kind"] == "internal_report"
                              else "external_assessment")
        for chunk in chunks[:MAX_CLAIMS_PER_REPORT]:
            claim = geo_claim_service.create_claim(
                client, claim_text=chunk["quoted_excerpt"], information_class=information_class,
                origin_type="document", origin_id=new_document["document_id"], actor_id=actor_id,
                source_id=source["source_id"])
            geo_claim_service.link_evidence(client, claim_id=claim["claim_id"], evidence_role="supporting",
                                             actor_id=actor_id, document_chunk_id=chunk["chunk_id"])
            for issue_id in issue_ids:
                geo_claim_service.link_claim_to_issue(client, claim_id=claim["claim_id"], issue_id=issue_id)
            claims_created.append(claim)
        skipped = max(0, len(chunks) - MAX_CLAIMS_PER_REPORT)

    geo_audit.log_audit(client, actor=actor_id, action="geo_report_version_create",
                         target_table="geo_reports", target_id=new_report["report_id"],
                         detail={"supersedes_id": supersedes_report_id, "version": new_report["version"],
                                 "document_id": new_document["document_id"]})

    return {"report": new_report, "document": new_document, "claims_created": len(claims_created),
            "chunks_skipped": skipped}


def diff_report_versions(client, report_id_a: str, report_id_b: str) -> dict:
    """2つのVersion間の決定的diff（LLM不使用、ルール7: 決定的処理とAzure OpenAI処理の分離）。
    extracted_textの行単位diff（difflib）とmetadata項目差分を返す。Phase4のLLMベースKnowledge
    抽出（extract_deltas）とは別物。"""
    report_a, report_b = get_report(client, report_id_a), get_report(client, report_id_b)
    if not report_a or not report_b:
        raise GeoReportError("report_idが見つかりません")

    text_a = (report_a.get("document") or {}).get("extracted_text") or ""
    text_b = (report_b.get("document") or {}).get("extracted_text") or ""
    text_diff = list(difflib.unified_diff(
        text_a.splitlines(), text_b.splitlines(),
        fromfile=f"v{report_a['version']}", tofile=f"v{report_b['version']}", lineterm="",
    ))

    fields_a, fields_b = _report_version_metadata_fields(report_a), _report_version_metadata_fields(report_b)
    metadata_diff = {k: {"from": fields_a[k], "to": fields_b.get(k)}
                      for k in fields_a if fields_a[k] != fields_b.get(k)}

    return {"report_a": report_a, "report_b": report_b, "text_diff": text_diff, "metadata_diff": metadata_diff}


def start_report_review_and_version(client, azure_client, model: str, *, report_id: str,
                                     reviewed_filename: str, reviewed_mime_type: str,
                                     reviewed_raw_bytes: bytes, reviewer_id: str,
                                     actor_id: str = "analyst") -> dict:
    """地政学アプリClooseout Phase5: Phase3（Versioning）とPhase4（Report Review Loop）を
    接続するオーケストレーション関数。新しいServiceは作らず、既存関数を呼び出し順に
    組み合わせるだけ:
        1. create_report_version(...)でレビュー後版（v2）を作成
        2. v1/v2それぞれのextracted_textをdraft_text/final_textとして
           geo_expert_review_service.start_review_session(report_id=v2のreport_id, ...)を呼ぶ
           （report_idはv1ではなく確定したv2を指す。v1はv2.supersedes_id経由で追跡する）
        3. extract_deltas(...)でKnowledge候補を抽出する
    Human Review（Promote/Reinforce/Reject）はこの関数の後、別途フロントエンドから
    review_session_id指定で呼ぶ（この関数は自動承認しない）。"""
    v1 = get_report(client, report_id)
    if not v1:
        raise GeoReportError(f"report_idが見つかりません: {report_id}")
    v1_text = (v1.get("document") or {}).get("extracted_text") or ""
    theme_id = (v1.get("document") or {}).get("theme_id")

    version_result = create_report_version(
        client, supersedes_report_id=report_id, filename=reviewed_filename,
        mime_type=reviewed_mime_type, raw_bytes=reviewed_raw_bytes, actor_id=actor_id,
    )
    v2_report_id = version_result["report"]["report_id"]
    v2_text = version_result["document"].get("extracted_text") or ""

    session = geo_expert_review_service.start_review_session(
        client, theme_id=theme_id, case_id=None, report_id=v2_report_id,
        draft_text=v1_text, final_text=v2_text, reviewer_id=reviewer_id,
        review_type="report_review",
    )
    extraction = geo_expert_review_service.extract_deltas(
        client, azure_client, model, session["review_session_id"], actor_id=actor_id)

    return {"version": version_result, "review_session": session, "extraction": extraction}


def _attach_document_summaries(client, reports: list) -> list:
    """一覧表示用に、各Reportへ紐づくgeo_documentsの表示項目（title/theme_id等）を一括join
    する（N+1を避けるためbulk select、Phase2 Intelligence Library統合）。"""
    document_ids = sorted({r["document_id"] for r in reports if r.get("document_id")})
    if not document_ids:
        return reports
    docs = client.select("geo_documents", {
        "select": "document_id,title,theme_id,published_at,fetched_at",
        "document_id": f"in.({','.join(document_ids)})",
    })
    docs_by_id = {d["document_id"]: d for d in docs}
    theme_ids = sorted({d["theme_id"] for d in docs if d.get("theme_id")})
    themes_by_id = {}
    if theme_ids:
        themes = client.select("geo_themes", {"select": "theme_id,name_ja", "theme_id": f"in.({','.join(theme_ids)})"})
        themes_by_id = {t["theme_id"]: t["name_ja"] for t in themes}
    for r in reports:
        doc = docs_by_id.get(r.get("document_id"))
        r["document_title"] = doc.get("title") if doc else None
        r["theme_name"] = themes_by_id.get(doc.get("theme_id")) if doc else None
    derive_report_review_status(client, reports)
    _attach_confidentiality_label(reports)
    return reports


def _attach_confidentiality_label(reports: list) -> None:
    """Phase 2E: 表示用のUI Label（部内限り/SHD限り/グループ配信可）を、フロントエンドが
    human_visibility/agent_sharing_policyのenum組み合わせを再実装しなくて済むよう、
    Backend側で導出して付与する（ロジックの二重実装を避ける、単一の正はgeo_knowledge_
    access_policy.derive_ui_label_from_policy）。どの3 Labelとも一致しない場合はNone。"""
    for r in reports:
        r["confidentiality_label"] = geo_knowledge_access_policy.derive_ui_label_from_policy(r)


def _latest_only(reports: list) -> list:
    """Report Versioning（Closeout Phase3）: Intelligence Libraryの一覧・検索は既定で
    report_identity_idごとのlatest versionのみを返す（過去versionはVersion履歴からのみ
    閲覧可能にする）。report_identity_id/versionが無い行（migration未適用の環境）はそのまま
    通す（後方互換）。"""
    latest_by_identity: dict = {}
    passthrough = []
    for r in reports:
        identity = r.get("report_identity_id")
        if identity is None:
            passthrough.append(r)
            continue
        current = latest_by_identity.get(identity)
        if current is None or (r.get("version") or 1) > (current.get("version") or 1):
            latest_by_identity[identity] = r
    return list(latest_by_identity.values()) + passthrough


def list_reports(client, *, gap_id: str = None, requester_context: dict = None) -> list:
    """Phase 2E: requester_context省略時は無フィルタ（既存の内部呼び出し元・Cross-Domain
    Agent呼び出し元は、Human Visibilityではなく別途Agent Sharing Policyで自前filterするため、
    ここで勝手にHuman向けfilterを混ぜない）。Human向けAPI（geo_report_api.py）だけが
    requester_contextを明示的に渡し、can_access_report(operation="list")で絞り込む。"""
    if gap_id:
        # Intelligence Needの解決に実際に紐づいた特定Versionを示すため、ここではlatest-only
        # 絞り込みを適用しない（過去versionが解決に使われた場合でもそのまま表示する）。
        links = client.select("geo_report_gaps", {"select": "report_id", "gap_id": f"eq.{gap_id}"})
        report_ids = sorted({l["report_id"] for l in links})
        if not report_ids:
            return []
        reports = client.select("geo_reports", {
            "select": "*", "report_id": f"in.({','.join(report_ids)})", "order": "uploaded_at.desc"})
        reports = _attach_document_summaries(client, reports)
    else:
        reports = client.select("geo_reports", {"select": "*", "order": "uploaded_at.desc"})
        reports = _attach_document_summaries(client, _latest_only(reports))
    if requester_context is not None:
        reports = geo_knowledge_access_policy.filter_human_accessible_reports(
            reports, requester_context=requester_context)
    return reports


def search_reports(client, query_text: str, *, include_demo: bool = False,
                    requester_context: dict = None) -> list:
    """Intelligence Library向けReport検索。既存geo_search_service.search_reports
    （汎用ILIKEヘルパーのみ、新規RPC・migrationなし）をそのまま呼び、一覧表示用のDocument
    summary（title/theme）を付加する。一覧同様、既定でlatest versionのみを返す。

    Phase 2E: requester_context省略時は無フィルタ（list_reportsと同じ理由、
    geo_department_intelligence_service.retrieve_department_intelligenceのようなCross-Domain
    Agent呼び出し元は自前でfilter_agent_accessible_reportsを適用する）。"""
    reports = geo_search_service.search_reports(client, query_text, include_demo=include_demo)
    reports = _attach_document_summaries(client, _latest_only(reports))
    if requester_context is not None:
        reports = geo_knowledge_access_policy.filter_human_accessible_reports(
            reports, requester_context=requester_context)
    return reports


def get_reports_by_ids(client, report_ids: list) -> list:
    """Phase 2F-1: Intelligence AI検索など、既にeligible/rankedなIDリストを持っている
    呼び出し元向けの公開エントリポイント。document_title/theme_name/review_status/
    confidentiality_labelの付与（_attach_document_summaries）を再利用し、metadata付与
    ロジックを重複実装しない。順序はreport_idsの順（呼び出し側でrankingを保持できる
    よう、DB取得順に依存しない）。"""
    if not report_ids:
        return []
    rows = client.select("geo_reports", {"select": "*", "report_id": f"in.({','.join(report_ids)})"})
    rows = _attach_document_summaries(client, rows)
    rows_by_id = {r["report_id"]: r for r in rows}
    return [rows_by_id[rid] for rid in report_ids if rid in rows_by_id]


def get_report_for_caller(client, report_id: str, *, requester_context: dict = None) -> dict | None:
    """Phase 2E §5D Direct-ID Access対策。get_report()自体は既存の内部呼び出し元（delete_report/
    create_report_version/diff_report_versions/list_report_versions等、Actor自身が既に対象
    report_idを把握している運用上の内部参照）向けに無変更のまま維持し、Human向けAPI
    （GET /api/geo/reports/{id}、/download、/versions、/latest）はこちらを経由させることで、
    Direct-ID既知の第三者が権限の無いReportを直接読めてしまう抜け道を塞ぐ。"""
    report = get_report(client, report_id)
    if not report:
        return None
    if not geo_knowledge_access_policy.can_access_report(
            report, operation="read", requester_context=requester_context):
        return None
    return report
