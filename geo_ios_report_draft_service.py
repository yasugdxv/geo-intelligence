"""Geopolitical Intelligence Operating System (IOS): Native Report Draft。

IOS自身が「Analysis Session → Native Draft(AI自動生成) → AI Review → Expert Review →
Approval」という本文生成〜レビューフローをIOS内で完結させるための中核サービス。既存
Report Studio（geo_report_projects/geo_report_syntheses）のテーブル・ロジックには一切
触れない——Native/Legacyの切り替えはgeo_ios_analysis_sessions.report_generation_modeという
明示的なフラグのみで判定し、Native Draftが存在するかどうかの暗黙判定はしない
（呼び出し元がこのフラグを見て分岐する。判定ロジック自体は呼び出し元＝geo_ios_review_api.py/
geo_ios_approval_api.py側に置く）。

【設計原則、実装時に必ず守ること】
1. Draft本文はimmutable — create_draft_versionのみが本文を作る。in-place更新のAPIは
   一切提供しない（AI生成・人間の手直しのどちらも常に新version）。
2. AI Reviewはupsertしない — create_ai_review_attemptは常に新しいreview_attemptを追記する。
3. Expert Review申請Gate（AI Review完了・open Critical findingなし）はここ
   （check_expert_review_gate）で判定し、呼び出し元API層がbypassできないようにする。
4. Approvalは常にExpert ReviewでFreezeされた内容のみを見る（get_approvable_snapshot）。
   Current Draftを承認時に再構築することは一切行わない。
"""
import hashlib
import json
import re
from datetime import datetime, timezone

import requests

import geo_audit
import geo_llm_common

DRAFT_SOURCES = ("ai_generated", "human_edit", "uploaded")
DRAFT_STATUSES = ("pending", "completed", "failed")
FINDING_STATUSES = ("open", "waived")

_MAX_NUMBER_RETRIES = 3


class ReportDraftError(Exception):
    """Native Report Draft/AI Reviewの操作エラー（400として扱う想定）。"""


class StaleDraftEditError(ReportDraftError):
    """Human Editのbase_report_draft_idがCurrent Draftと不一致（409として扱う想定）。"""


class ExpertReviewGateError(ReportDraftError):
    """Expert Review申請Gateの不成立（400として扱う想定）。"""


def _is_unique_violation(exc: Exception) -> bool:
    resp = getattr(exc, "response", None)
    if resp is None:
        return False
    if resp.status_code == 409:
        return True
    try:
        return resp.json().get("code") == "23505"
    except Exception:
        return "23505" in (resp.text or "")


def _compute_content_hash(sections: list) -> str:
    """sectionsを正規化(heading/body/supporting_evidence_idsのみ、順序固定)した
    JSONのsha256。completedになった時点で1回だけ計算し、以後はコピーして使い回す
    （Review Cycle/Approvalの整合性チェックの基準値、再計算しない）。"""
    normalized = [
        {
            "heading": s.get("heading"),
            "body": s.get("body"),
            "supporting_evidence_ids": sorted(s.get("supporting_evidence_ids") or []),
        }
        for s in (sections or [])
    ]
    payload = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---- Report Draft（version追記のみ、in-place上書き禁止） -------------------

def get_current_draft(client, analysis_session_id: str) -> dict | None:
    rows = client.select("geo_ios_report_drafts", {
        "select": "*", "analysis_session_id": f"eq.{analysis_session_id}",
        "order": "version.desc", "limit": "1"})
    return rows[0] if rows else None


def get_draft(client, report_draft_id: str) -> dict | None:
    rows = client.select("geo_ios_report_drafts", {
        "select": "*", "report_draft_id": f"eq.{report_draft_id}", "limit": "1"})
    return rows[0] if rows else None


def list_draft_history(client, analysis_session_id: str) -> list:
    return client.select("geo_ios_report_drafts", {
        "select": "*", "analysis_session_id": f"eq.{analysis_session_id}", "order": "version.asc"})


def create_draft_version(client, analysis_session_id: str, *, source: str, created_by: str,
                          sections: list | None = None, base_report_draft_id: str | None = None,
                          change_note: str | None = None) -> dict:
    """新しいDraft versionを作る（in-place上書きは提供しない）。

    source="human_edit": sections必須。base_report_draft_idも必須で、Current Draftの
    report_draft_idと一致しなければStaleDraftEditErrorを投げる（古いVersionを開いたまま
    別の変更が入ったstale editを防ぐ）。渡されたsectionsで即status="completed"。
    source="ai_generated": sectionsは渡さない（背景生成が後でmark_draft_completedする）。
    status="pending"で作成し、parent_report_draft_idはCurrent Draftのid（無ければNone）。
    """
    if source not in DRAFT_SOURCES:
        raise ReportDraftError(f"未対応のsourceです: {source}（許可: {DRAFT_SOURCES}）")

    current = get_current_draft(client, analysis_session_id)

    if source == "human_edit":
        if not sections:
            raise ReportDraftError("human_editにはsectionsが必須です")
        current_id = current["report_draft_id"] if current else None
        if base_report_draft_id != current_id:
            raise StaleDraftEditError(
                "base_report_draft_idがCurrent Draftと一致しません。最新のDraftを再読込してください"
                f"（渡された値: {base_report_draft_id}, Current: {current_id}）")

    parent_id = current["report_draft_id"] if current else None
    completed_at = datetime.now(timezone.utc).isoformat() if source == "human_edit" else None
    content_hash = _compute_content_hash(sections) if source == "human_edit" else None

    last_exc = None
    for _ in range(_MAX_NUMBER_RETRIES):
        current = get_current_draft(client, analysis_session_id)
        next_version = (current["version"] + 1) if current else 1
        row = {
            "analysis_session_id": analysis_session_id, "version": next_version,
            "parent_report_draft_id": parent_id, "source": source,
            "status": "completed" if source == "human_edit" else "pending",
            "sections": sections if source == "human_edit" else None,
            "content_hash": content_hash, "change_note": change_note, "created_by": created_by,
            "completed_at": completed_at,
        }
        try:
            created = client.insert("geo_ios_report_drafts", [row])[0]
        except requests.exceptions.HTTPError as e:
            if _is_unique_violation(e):
                last_exc = e
                continue
            raise
        geo_audit.log_audit(
            client, actor=created_by, action="report_draft_new_version",
            target_table="geo_ios_report_drafts", target_id=created["report_draft_id"],
            detail={"analysis_session_id": analysis_session_id, "version": next_version, "source": source})
        return created
    raise ReportDraftError("Draft versionの採番が競合し続けました。もう一度お試しください") from last_exc


_IMPORT_SCHEMA_NAME = "geo_ios_draft_import_v2"

# ---- アップロード取込の条件（API・画面の事前チェックと同じ値。画面側は lib/iosUploadLimits.ts） ----
IMPORT_ALLOWED_EXTENSIONS = (".pdf", ".docx", ".txt", ".html", ".htm")
IMPORT_MAX_FILE_BYTES = 20 * 1024 * 1024  # 20MB
IMPORT_MAX_TEXT_CHARS = 150_000  # 抽出後の本文。これを超えると後段のAIレビューが成立しにくい
# AIに章立てを判断させる本文の上限。これを超える場合はAIを使わず、見出し・段落でコードが分割する
_IMPORT_AI_MAX_CHARS = 120_000
_IMPORT_FALLBACK_CHUNK_CHARS = 6000


def validate_import_file(filename: str, size_bytes: int) -> str | None:
    """アップロード前の形式・サイズ確認。問題があれば利用者向けメッセージを返す（問題なしはNone）。"""
    name = (filename or "").lower()
    if not name.endswith(IMPORT_ALLOWED_EXTENSIONS):
        return ("対応していないファイル形式です。PDF・Word(.docx)・HTML・テキスト(.txt)のいずれかを"
                "アップロードしてください（古いWord形式 .doc は .docx に変換してください）。")
    if size_bytes <= 0:
        return "ファイルが空です。内容のあるファイルをアップロードしてください。"
    if size_bytes > IMPORT_MAX_FILE_BYTES:
        mb = size_bytes / (1024 * 1024)
        return (f"ファイルサイズが大きすぎます（{mb:.1f}MB）。上限は"
                f"{IMPORT_MAX_FILE_BYTES // (1024 * 1024)}MBです。画像を減らす・分割するなどして小さくしてください。")
    return None


def validate_import_text(text: str) -> str | None:
    """本文抽出後の長さ確認。問題があれば利用者向けメッセージを返す（問題なしはNone）。"""
    n = len((text or "").strip())
    if n == 0:
        return ("文書から本文を取り出せませんでした。画像だけのPDF（スキャン）は読み取れません。"
                "文字を選択できるPDFか、Word・テキストでアップロードしてください。")
    if n > IMPORT_MAX_TEXT_CHARS:
        return (f"本文が長すぎます（約{n // 10000}万文字）。上限は約{IMPORT_MAX_TEXT_CHARS // 10000}万文字です。"
                "章ごとに分けてアップロードしてください。")
    return None


def _build_import_schema() -> dict:
    """改訂生成(run_review_revision)が使う、見出しと本文の組を返させるSchema。"""
    return {
        "type": "object",
        "properties": {
            "draft_sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"heading": {"type": "string"}, "body": {"type": "string"}},
                    "required": ["heading", "body"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["draft_sections"],
        "additionalProperties": False,
    }


def _build_outline_schema() -> dict:
    """AIには「章の見出し」と「その章が元文書で始まる箇所の原文（冒頭の数十文字）」だけを返させる。
    本文はコードが元テキストから切り出すため、文書が長くてもAIの出力は短く、本文は一字も変わらない。"""
    return {
        "type": "object",
        "properties": {
            "outline": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "heading": {"type": "string"},
                        "start_text": {"type": "string"},
                    },
                    "required": ["heading", "start_text"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["outline"],
        "additionalProperties": False,
    }


_HEADING_LINE = re.compile(
    r"^\s*(?:#{1,6}\s+\S|第[0-9０-９一二三四五六七八九十]+[章節部]|[0-9０-９]+[\.．)）]\s*\S|"
    r"[0-9０-９]+\.[0-9０-９]+\s*\S|[■□●◆▼【]\s*\S|[IVX]+\.\s+\S)")


def _is_heading_line(line: str, next_line: str = "") -> bool:
    """見出しらしい行か。記号付き（第1章・■・# 等）に加え、記号が無くても
    「短く・句点で終わらず・表や箇条書きでなく・次の行が長い本文」なら見出しとみなす。"""
    t = line.strip()
    if not (0 < len(t) <= 50) or t.endswith(("。", "．", ".", "、", "：", ":")):
        return False
    if _HEADING_LINE.match(t):
        return True
    if t.startswith(("|", "-", "・", "*", "(", "（")) or "|" in t[1:]:
        return False
    return len(t) <= 40 and len(next_line.strip()) >= 30


def _paragraphs(text: str) -> list:
    """空行区切りの段落。空行が無い文書（改行だけ）は行ごとを段落として扱う。"""
    paras = [p_ for p_ in re.split(r"\n\s*\n", text) if p_.strip()]
    return paras if len(paras) > 1 else [ln for ln in text.split("\n") if ln.strip()]


def _pack_paragraphs(paras: list, heading: str) -> list:
    """段落を約6000文字ごとにまとめて章にする（段落の途中では切らない）。"""
    chunks, cur, size = [], [], 0
    for para in paras:
        if size and size + len(para) > _IMPORT_FALLBACK_CHUNK_CHARS:
            chunks.append("\n".join(cur).strip())
            cur, size = [], 0
        cur.append(para)
        size += len(para)
    if cur:
        chunks.append("\n".join(cur).strip())
    if len(chunks) == 1:
        return [{"heading": heading, "body": chunks[0]}]
    return [{"heading": f"{heading}（{i}/{len(chunks)}）", "body": c} for i, c in enumerate(chunks, 1)]


def split_text_without_ai(raw_text: str) -> list:
    """AIを使わない分割。見出しらしい行で区切り、長い章は約6000文字ごとに区切る。
    見出しが見つからない文書は段落（無ければ行）境界で区切る。
    本文は元のまま（要約・改変なし）。AIが失敗した場合・極端に長い場合の確実な代替。"""
    lines = raw_text.split("\n")
    sections: list = []
    heading = None
    buf: list = []

    def flush():
        body = "\n".join(buf).strip()
        if body:
            sections.append({"heading": heading or "冒頭", "heading_found": heading is not None, "body": body})

    for i, line in enumerate(lines):
        nxt = next((x for x in lines[i + 1:i + 3] if x.strip()), "")
        if _is_heading_line(line, nxt):
            flush()
            heading, buf = line.strip().lstrip("#").strip(), []
        else:
            buf.append(line)
    flush()
    if not any(x["heading_found"] for x in sections):
        return _pack_paragraphs(_paragraphs(raw_text), "パート")
    out: list = []
    for sec in sections:
        if len(sec["body"]) <= _IMPORT_FALLBACK_CHUNK_CHARS * 2:
            out.append({"heading": sec["heading"], "body": sec["body"]})
        else:
            out.extend(_pack_paragraphs(_paragraphs(sec["body"]), sec["heading"]))
    return _merge_small_sections(out)


def _merge_small_sections(sections: list) -> list:
    """小さすぎる章（約1500文字未満）を、直前の章へ見出しごと取り込む（合計が約6000文字まで）。
    見出し行が細かく誤検出されても、章が何百個にも増えないようにする。"""
    merged: list = []
    for sec in sections:
        if merged and len(merged[-1]["body"]) < 1500 and \
                len(merged[-1]["body"]) + len(sec["body"]) <= _IMPORT_FALLBACK_CHUNK_CHARS:
            merged[-1]["body"] += f"\n{sec['heading']}\n{sec['body']}"
        else:
            merged.append(dict(sec))
    return merged


def slice_sections_by_outline(raw_text: str, outline: list) -> list:
    """AIが返した目印（start_text）を元テキスト内で順に探し、その間の本文を切り出す。
    見つからない章は読み飛ばす。1つも見つからなければ空リスト（呼び出し側が代替分割へ）。"""
    found: list = []  # (位置, 見出し, 目印)
    cursor = 0
    for item in outline:
        heading = (item.get("heading") or "").strip()
        start = (item.get("start_text") or "").strip()
        pos = -1
        for key in (start, heading):
            if key:
                pos = raw_text.find(key, cursor)
                if pos >= 0:
                    start = key
                    break
        if pos < 0:
            continue
        found.append((pos, heading or start, start))
        cursor = pos + max(1, len(start))
    if not found:
        return []
    sections: list = []
    if found[0][0] > 0 and raw_text[:found[0][0]].strip():
        sections.append({"heading": "冒頭", "body": raw_text[:found[0][0]].strip()})
    for i, (pos, heading, start) in enumerate(found):
        end = found[i + 1][0] if i + 1 < len(found) else len(raw_text)
        body = raw_text[pos:end].strip()
        # 本文の先頭が見出しそのものなら、見出し欄と重複するので取り除く
        if heading and body.startswith(heading):
            body = body[len(heading):].lstrip(" \t\r\n：:-－—")
        sections.append({"heading": heading, "body": body})
    return sections


def run_document_import(azure_client, model, *, raw_text: str, theme: str, max_attempts: int = 2) -> list:
    """アップロードされた文書のテキストを、既存draft_sections形式（[{heading, body}]）へ分割する。
    地政学分析ではなく文書構造化タスクのため、Expert AI Engine（geo_expert_engine.py）は
    経由せず、geo_llm_common.call_llm_structuredを直接呼ぶ。

    AIには章立て（見出しと開始位置の原文）だけを返させ、本文は元テキストからコードが切り出す
    ——長い文書でもAIの出力上限に当たらず、本文は一字も変わらない。
    本文が長すぎる・AIが失敗した・目印が1つも見つからない場合は、AIを使わない分割に切り替える。"""
    if len(raw_text) > _IMPORT_AI_MAX_CHARS:
        return split_text_without_ai(raw_text)
    system_prompt = (
        "あなたは文書構造化アシスタントです。与えられた文書の章立て（見出しの一覧）を抽出する"
        "役割を担います。本文を書き写したり、要約・言い換え・新しい主張や推測の追加を行ったり"
        "しないでください。"
    )
    user_prompt = (
        f"以下は、テーマ「{theme}」に関連してユーザーがアップロードした文書の全文です。\n"
        "文書の章立てを、出現順にoutlineとして返してください。各要素は次の2つです。\n"
        "- heading: 章・節の見出し（文書にある見出しをそのまま。無い場合は内容を表す短い題）\n"
        "- start_text: その章が文書内で始まる箇所の原文を、冒頭から20〜40文字そのままコピーしたもの"
        "（一字も変えない。文書内でその位置を特定するために使う）\n"
        "本文そのものは出力しないでください。章は多くても30個程度にまとめてください。\n\n"
        f"## 文書全文\n{raw_text}"
    )
    try:
        result = geo_llm_common.call_llm_structured(
            azure_client, model, system_prompt, user_prompt, _build_outline_schema(), _IMPORT_SCHEMA_NAME,
            max_attempts=max_attempts)
        sections = slice_sections_by_outline(raw_text, result["data"]["outline"])
    except geo_llm_common.GeoLLMError:
        sections = []
    return sections if sections else split_text_without_ai(raw_text)


_REVIEW_REVISION_SCHEMA_NAME = "geo_ios_review_revision_v1"

# REVIEW MODE §11 Revision Strategy（2026-09-25追加）。3方針のみ、Case固有の言い回しは
# 入れない——一般化された指示文のみ。Expert AI Engineは経由しない
# （run_document_importと同じ軽量LLM呼び出しパターン）。
_REVISION_STRATEGY_INSTRUCTIONS = {
    "keep_and_reinforce": (
        "現在の読み（Primary Reading）の結論自体は維持してください。Evidence・Actor分析・"
        "反証条件・Scenarioの記述のみを、下記の補強材料でより厚くしてください。結論を"
        "変更しないでください。"
    ),
    "replace": (
        "現在の読み（Primary Reading）を、下記の新しい読み（Target Reading）に置き換えて"
        "レポート全体を書き直してください。"
    ),
    "integrate": (
        "現在の読み（Primary Reading）を土台として維持しつつ、下記の新しい読み"
        "（Target Reading）の要素を統合してください。両方の視点が反映された結論にして"
        "ください。"
    ),
}


def run_review_revision(azure_client, model, *, original_report_text: str, original_reading: str,
                         target_reading: str, strategy: str, theme: str,
                         max_attempts: int = 2) -> list:
    """REVIEW MODE Step3「レポートをリバイズ」。元のレポート全文＋修正方針＋Current/Target
    Readingから、既存draft_sections形式（[{heading, body}]）でリバイズ後の本文を生成する。
    Expert AI Engine（geo_expert_engine.py）は経由しない——文書改訂タスクとして
    run_document_importと同じ既存パターンでgeo_llm_common.call_llm_structuredを直接呼ぶ。
    指定されていない事実の新規追加は指示で禁止する。"""
    if strategy not in _REVISION_STRATEGY_INSTRUCTIONS:
        raise ReportDraftError(f"未対応のrevision strategyです: {strategy}")
    schema = _build_import_schema()
    instruction = _REVISION_STRATEGY_INSTRUCTIONS[strategy]
    system_prompt = (
        "あなたはレポート改訂アシスタントです。与えられた元のレポート全文を、指定された"
        "修正方針に従って改訂し、見出し(heading)と本文(body)の組のリストとして返して"
        "ください。元のレポート・Current Reading・Target Readingのいずれにも無い具体的"
        "事実・数値・Evidenceを新しく作らないでください。"
    )
    user_prompt = (
        f"# テーマ\n{theme}\n\n"
        f"# 修正方針\n{instruction}\n\n"
        f"# 現在の読み（Primary Reading）\n{original_reading}\n\n"
        f"# 新しい読み（Target Reading）\n{target_reading}\n\n"
        f"# 元のレポート全文\n{original_report_text}"
    )
    result = geo_llm_common.call_llm_structured(
        azure_client, model, system_prompt, user_prompt, schema, _REVIEW_REVISION_SCHEMA_NAME,
        max_attempts=max_attempts)
    return result["data"]["draft_sections"]


def mark_draft_completed(client, report_draft_id: str, *, sections: list,
                          expert_request_id: str, expert_response_id: str) -> dict:
    client.update("geo_ios_report_drafts", {"report_draft_id": f"eq.{report_draft_id}"}, {
        "status": "completed", "sections": sections, "content_hash": _compute_content_hash(sections),
        "expert_request_id": expert_request_id, "expert_response_id": expert_response_id,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })
    return get_draft(client, report_draft_id)


def mark_draft_failed(client, report_draft_id: str, *, error_message: str) -> dict:
    client.update("geo_ios_report_drafts", {"report_draft_id": f"eq.{report_draft_id}"}, {
        "status": "failed", "error_message": (error_message or "")[:2000],
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })
    return get_draft(client, report_draft_id)


def has_completed_draft(client, analysis_session_id: str) -> bool:
    current = get_current_draft(client, analysis_session_id)
    return bool(current and current.get("status") == "completed")


# ---- Draft AI Review（append-only履歴、upsertしない） -----------------------

def _next_review_attempt(client, report_draft_id: str) -> int:
    existing = client.select("geo_ios_draft_ai_reviews", {
        "select": "review_attempt", "report_draft_id": f"eq.{report_draft_id}",
        "order": "review_attempt.desc", "limit": "1"})
    return (existing[0]["review_attempt"] + 1) if existing else 1


def create_ai_review_attempt(client, report_draft_id: str, *, requested_by: str) -> dict:
    """常に新規review_attemptを追記する（upsertしない）。"""
    last_exc = None
    for _ in range(_MAX_NUMBER_RETRIES):
        attempt = _next_review_attempt(client, report_draft_id)
        row = {
            "report_draft_id": report_draft_id, "review_attempt": attempt,
            "status": "pending", "requested_by": requested_by,
        }
        try:
            created = client.insert("geo_ios_draft_ai_reviews", [row])[0]
        except requests.exceptions.HTTPError as e:
            if _is_unique_violation(e):
                last_exc = e
                continue
            raise
        geo_audit.log_audit(
            client, actor=requested_by, action="draft_ai_review_request",
            target_table="geo_ios_draft_ai_reviews", target_id=created["draft_ai_review_id"],
            detail={"report_draft_id": report_draft_id, "review_attempt": attempt})
        return created
    raise ReportDraftError("AI Review attemptの採番が競合し続けました。もう一度お試しください") from last_exc


def get_ai_review(client, draft_ai_review_id: str) -> dict | None:
    rows = client.select("geo_ios_draft_ai_reviews", {
        "select": "*", "draft_ai_review_id": f"eq.{draft_ai_review_id}", "limit": "1"})
    return rows[0] if rows else None


def get_latest_ai_review(client, report_draft_id: str) -> dict | None:
    rows = client.select("geo_ios_draft_ai_reviews", {
        "select": "*", "report_draft_id": f"eq.{report_draft_id}",
        "order": "review_attempt.desc", "limit": "1"})
    return rows[0] if rows else None


def list_ai_review_history(client, report_draft_id: str) -> list:
    return client.select("geo_ios_draft_ai_reviews", {
        "select": "*", "report_draft_id": f"eq.{report_draft_id}", "order": "review_attempt.asc"})


def mark_ai_review_completed(client, draft_ai_review_id: str, *, findings: list,
                              expert_request_id: str, expert_response_id: str) -> dict:
    """既存Expert AI(draft_reviewモード)のreview_findings（finding_id/statusを持たない）に、
    finding_id（連番、waive対象を指すためだけの決定的ID）とstatus="open"を付与してから
    保存する。既存Expert AI Schema自体は変更しない（このIDはAdapterの外、永続化層でのみ
    付与する）。"""
    normalized = []
    for i, f in enumerate(findings or []):
        normalized.append({
            **f, "finding_id": f.get("finding_id") or f"f{i + 1}", "status": "open",
            "waiver_reason": None, "waived_by": None, "waived_at": None,
        })
    client.update("geo_ios_draft_ai_reviews", {"draft_ai_review_id": f"eq.{draft_ai_review_id}"}, {
        "status": "completed", "findings": normalized,
        "expert_request_id": expert_request_id, "expert_response_id": expert_response_id,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })
    return get_ai_review(client, draft_ai_review_id)


def mark_ai_review_failed(client, draft_ai_review_id: str, *, error_message: str) -> dict:
    client.update("geo_ios_draft_ai_reviews", {"draft_ai_review_id": f"eq.{draft_ai_review_id}"}, {
        "status": "failed", "error_message": (error_message or "")[:2000],
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })
    return get_ai_review(client, draft_ai_review_id)


def waive_finding(client, draft_ai_review_id: str, *, finding_id: str, waiver_reason: str,
                   waived_by: str) -> dict:
    """本文を直さずに「解決済み」にする設計は提供しない——採用しない指摘だけ、理由を必須で
    明示的にwaiveできる。"""
    if not (waiver_reason or "").strip():
        raise ReportDraftError("waiver_reasonは必須です")
    review = get_ai_review(client, draft_ai_review_id)
    if not review:
        raise ReportDraftError(f"draft_ai_review_idが見つかりません: {draft_ai_review_id}")
    findings = review.get("findings") or []
    found = False
    waived_at = datetime.now(timezone.utc).isoformat()
    updated = []
    for f in findings:
        if f.get("finding_id") == finding_id:
            f = {**f, "status": "waived", "waiver_reason": waiver_reason,
                 "waived_by": waived_by, "waived_at": waived_at}
            found = True
        updated.append(f)
    if not found:
        raise ReportDraftError(f"finding_idが見つかりません: {finding_id}")

    client.update("geo_ios_draft_ai_reviews", {"draft_ai_review_id": f"eq.{draft_ai_review_id}"},
                  {"findings": updated})
    geo_audit.log_audit(
        client, actor=waived_by, action="draft_ai_review_finding_waive",
        target_table="geo_ios_draft_ai_reviews", target_id=draft_ai_review_id,
        detail={"finding_id": finding_id, "waiver_reason": waiver_reason})
    return get_ai_review(client, draft_ai_review_id)


# ---- Expert Review申請Gate（Backendで強制） ---------------------------------

def is_ai_review_passed(client, analysis_session_id: str) -> bool:
    """check_expert_review_gateと同じ判定条件を、例外を投げずbool返却で行う版。
    Workflow Header/Board用のStep状態表示にのみ使う（Gate自体はcheck_expert_review_gate
    が引き続き担い、こちらは表示専用の弱い版として重複させる——例外制御をUI集計に
    混ぜたくないため）。"""
    current = get_current_draft(client, analysis_session_id)
    if not current or current.get("status") != "completed":
        return False
    latest_review = get_latest_ai_review(client, current["report_draft_id"])
    if latest_review is None or latest_review.get("status") != "completed":
        return False
    open_critical = [
        f for f in (latest_review.get("findings") or [])
        if f.get("severity") == "Critical" and f.get("status") == "open"
    ]
    return not open_critical


def check_expert_review_gate(client, analysis_session_id: str) -> dict:
    """Current Draftに対する最新AI Review attemptが (a) 存在し (b) status=="completed" (c)
    open Critical findingが無いことを確認する。いずれか不成立ならExpertReviewGateErrorを
    投げる。成立時はCurrent Draftの行を返す（呼び出し元がそのまま使えるように）。"""
    current = get_current_draft(client, analysis_session_id)
    if not current or current.get("status") != "completed":
        raise ExpertReviewGateError("Draftがまだ生成されていません")

    latest_review = get_latest_ai_review(client, current["report_draft_id"])
    if latest_review is None:
        raise ExpertReviewGateError("AI Reviewが未実施です。先にAI Reviewを実行してください")
    if latest_review.get("status") == "pending":
        raise ExpertReviewGateError("AI Review実行中です。完了までお待ちください")
    if latest_review.get("status") == "failed":
        raise ExpertReviewGateError("AI Reviewが失敗しています。再実行してください")

    open_critical = [
        f for f in (latest_review.get("findings") or [])
        if f.get("severity") == "Critical" and f.get("status") == "open"
    ]
    if open_critical:
        raise ExpertReviewGateError(
            f"未対応のCritical指摘が{len(open_critical)}件残っています。"
            "採用しない場合はWaive（理由必須）してください")
    return current


def build_native_review_snapshot(client, analysis_session_id: str) -> dict:
    """check_expert_review_gateを通過した後にのみ呼ぶこと。Current Draftの内容をFreezeし、
    既存コンシューマ契約（version_snapshot.syntheses[].final_html/final_status）と同じ形を
    再現する。final_htmlはHTMLエスケープしてから組み立てる（AI生成テキストが
    dangerouslySetInnerHTMLで表示されるため、Stored XSS対策として必須）。"""
    import html as _html

    current = check_expert_review_gate(client, analysis_session_id)
    sections = current.get("sections") or []
    body_html = "".join(
        f"<h2>{_html.escape(s.get('heading') or '')}</h2>"
        f"<p>{_html.escape(s.get('body') or '')}</p>"
        for s in sections
    )
    version_label = f"draft:v{current['version']}"
    return {
        "version_label": version_label,
        "version_snapshot": {
            "source": "ios_native_draft",
            "report_draft_id": current["report_draft_id"],
            "draft_version": current["version"],
            "syntheses": [{
                "signal_id": None, "narrative_id": None, "final_status": "final",
                "final_html": body_html, "synthesis_created_at": current.get("completed_at"),
            }],
        },
        "report_draft_id": current["report_draft_id"],
        "draft_version": current["version"],
        "content_hash": current["content_hash"],
    }


# ---- Approval — Freezeされた内容のみを承認可能にする ------------------------

def get_approvable_snapshot(client, analysis_session_id: str) -> dict:
    """Current Draftと同一のReview Cycle（source_report_draft_id/source_draft_version/
    source_content_hashが一致）のうち最新のものを取得し、そのresultがapprovedである場合のみ
    version_snapshot等を返す。Current Draftを再構築することは一切行わない——Approvalは常に
    Expert ReviewでFreezeされた内容のみを見る。"""
    current = get_current_draft(client, analysis_session_id)
    if not current or current.get("status") != "completed":
        raise ReportDraftError("Draftがまだ生成されていません")

    cycles = client.select("geo_ios_review_cycles", {
        "select": "*", "source_report_draft_id": f"eq.{current['report_draft_id']}",
        "source_draft_version": f"eq.{current['version']}",
        "source_content_hash": f"eq.{current['content_hash']}",
        "order": "round_number.desc", "limit": "1"})
    if not cycles:
        raise ReportDraftError("このDraftはまだExpert Reviewに提出されていません")

    latest_cycle = cycles[0]
    if latest_cycle.get("result") != "approved":
        raise ReportDraftError(
            f"最新のExpert Review結果はapprovedではありません（result={latest_cycle.get('result')}）")

    return {
        "version_label": latest_cycle.get("reviewed_version_label"),
        "version_snapshot": latest_cycle.get("version_snapshot"),
        "report_draft_id": latest_cycle.get("source_report_draft_id"),
        "draft_version": latest_cycle.get("source_draft_version"),
        "content_hash": latest_cycle.get("source_content_hash"),
    }
