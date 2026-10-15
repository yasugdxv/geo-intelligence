"""Geopolitical Intelligence OS: 前提整理（Intake）Chat + Analysis Context + Session Attachment。

「1 設計」ページを、専門家AIへ分析を依頼する前に担当者とAIがAnalysis Contextを十分に
揃えるIntake画面へ改修する（ユーザー指示、2026-09-24）。本モジュールが呼ぶLLMは
geo_llm_common.call_llm_structuredによる軽量な会話用呼び出しであり、Expert AI本体
（geo_expert_engine.py）・CQASロジックには一切触れない——前提整理チャットは「担当者と
AIが数回のQ&Aで前提を揃える」ためだけの別レイヤーであり、地政学的な読み方の推論は
次段階（Scenario生成、既存geo_ios_expert_ai_adapter経由）が担う。

Analysis Context（geo_ios_analysis_contexts）は、既存geo_ios_analysis_designsと同じ
「version追記のみ、上書きしない」パターンを踏襲する。ユーザーが全項目を直接入力する
フォームではなく、AIが会話から整理した「現在の理解」を毎ターン新versionとして記録する。
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import geo_audit
import geo_documents_store
import geo_llm_common
from config_utils import upload_base

INTAKE_UPLOAD_ROOT = upload_base() / "geo_ios_intake"

# pptx/xlsxは今回のスコープでは本文抽出非対応（ユーザー判断、2026-09-24）。
# ファイルの保存・一覧表示のみ行い、extraction_status='unsupported'として記録する。
_UNSUPPORTED_EXTRACTION_EXTENSIONS = (".pptx", ".xlsx")

CONTEXT_FIELDS = (
    "analysis_objective", "core_question", "current_situation", "business_context",
    "time_horizon", "geographic_scope", "relevant_actors", "key_actors_detail", "known_events",
    "user_assumptions", "must_consider_topics", "internal_context", "reference_materials",
    "constraints", "information_gaps",
)

_CONTEXT_LIST_FIELDS = (
    "relevant_actors", "known_events", "user_assumptions", "must_consider_topics",
    "reference_materials", "constraints", "information_gaps",
)

# key_actors_detail: ユーザー指示（2026-09-24、Step2品質検証の結果を受けた改善①）。
# relevant_actorsが「イラン政府」「米国政府」のような機関レベルに留まり、資料中に実在する
# 個人名・役職・レバレッジ・関係性・制約まで拾えていなかった（Gold Case Blind Testで確認済みの
# 実際のギャップ）ことへの対応。担当者に入力させるのではなく、会話・添付資料からAIが抽出する
# という既存方針を維持したまま、relevant_actorsとは別のオブジェクト配列として持つ
# （文字列配列のrelevant_actorsを壊さない）。
_KEY_ACTORS_DETAIL_FIELDS = ("name", "role", "controls_or_leverage", "key_relationships", "constraints",
                             "notable_actions")

# 添付資料の構造化要約（ユーザー指示、2026-09-24b、Hungary Gold Case Blind Testで発覚した欠陥
# への対応）: _format_attachments_for_promptが添付資料の先頭2000文字だけをIntake Chatへ
# 見せていたため、Hungary資料（4316文字）の後半にあるPutin/Trump・MAGA関連の記述が
# 何ターン重ねてもAIに一度も見えていなかった（Actor Hitが弱くなった実根拠のある原因）。
# 単純に上限文字数を増やすと、資料が更に長くなった場合に同じ問題が形を変えて再発するため、
# 資料全文をchunk分割→断片ごとに人物・事実を抽出→1つに統合、という構成にする。
# Expert AI/CQAS/Reading Portfolioには一切触れない（Intake Chatへ渡す添付資料の表現方法だけ）。
_ATTACHMENT_CHUNK_CHARS = 6000

_ATTACHMENT_STRUCTURED_CONTEXT_SCHEMA_NAME = "geo_ios_attachment_structured_context"
_ATTACHMENT_STRUCTURED_CONTEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "key_actors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "controls_or_leverage": {"type": "string"},
                    "key_relationships": {"type": "string"},
                    "constraints": {"type": "string"},
                    "notable_actions": {"type": "string"},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
        "relevant_facts_or_events": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["key_actors", "relevant_facts_or_events"],
    "additionalProperties": False,
}

_ATTACHMENT_CHUNK_SYSTEM_PROMPT = """あなたは地政学インテリジェンス資料の下読み担当です。
以下は、ある資料の一部（分割された断片）です。この断片の中だけから、以下を抽出してください
（資料全体の他の部分は見えていないため、この断片に書かれている内容だけを対象にする）。

- key_actors: 断片中に個人名・具体的な役職が登場する人物ごとに:
  - name: 人物名
  - role: 役職・立場
  - controls_or_leverage: その人物が何を動かせるか・持っているレバレッジ
  - key_relationships: 誰との関係が重要か
  - constraints: その人物にかかっている制約
  - notable_actions: この断片中でその人物が行った・言ったとされる具体的な言動
  個人名が登場しない断片ではkey_actorsを空配列にしてよい（無理に埋めない）。
- relevant_facts_or_events: この断片に書かれている重要な事実・出来事（配列）。

捏造しないこと。この断片に書かれていないことは書かないこと。"""

_ATTACHMENT_MERGE_SYSTEM_PROMPT = """あなたは地政学インテリジェンス資料の統合担当です。
以下は、同じ1つの資料を複数の断片に分割し、断片ごとに個別抽出したkey_actors/
relevant_facts_or_eventsのリストです。同一人物が複数の断片にまたがって登場している場合は
1人分へ統合し（役職・レバレッジ・関係性・制約・言動は、断片間で矛盾しない範囲で最も
情報量の多い記述を採用するか、意味を変えずに1つの記述へ結合する）、重複した事実・出来事も
1つにまとめてください。

新しい情報を作り出さないこと（あくまで統合であり、追加の推測をしないこと）。"""


def _chunk_attachment_text(text: str, chunk_chars: int | None = None) -> list[str]:
    """textをchunk_chars以下の断片へ分割する。改行位置で区切ることで、文の途中で
    切れる頻度を下げる（厳密な文境界検出はしない、簡易な決定的分割で十分）。
    chunk_chars省略時は呼び出し時点の_ATTACHMENT_CHUNK_CHARSを使う（デフォルト引数に
    束縛すると、テストでのmonkeypatchが効かなくなるため、関数内で参照する）。"""
    if chunk_chars is None:
        chunk_chars = _ATTACHMENT_CHUNK_CHARS
    if len(text) <= chunk_chars:
        return [text]
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_chars, n)
        if end < n:
            newline_pos = text.rfind("\n", start, end)
            if newline_pos > start:
                end = newline_pos
        chunks.append(text[start:end])
        start = end
    return chunks


def _extract_structured_context(azure_client, model: str, extracted_text: str) -> dict:
    chunks = _chunk_attachment_text(extracted_text)
    chunk_results = []
    for chunk in chunks:
        result = geo_llm_common.call_llm_structured(
            azure_client, model, _ATTACHMENT_CHUNK_SYSTEM_PROMPT, chunk,
            _ATTACHMENT_STRUCTURED_CONTEXT_SCHEMA, _ATTACHMENT_STRUCTURED_CONTEXT_SCHEMA_NAME)
        chunk_results.append(result["data"])

    if len(chunk_results) == 1:
        return chunk_results[0]

    merge_input = json.dumps(chunk_results, ensure_ascii=False, indent=2)
    merged = geo_llm_common.call_llm_structured(
        azure_client, model, _ATTACHMENT_MERGE_SYSTEM_PROMPT, merge_input,
        _ATTACHMENT_STRUCTURED_CONTEXT_SCHEMA, _ATTACHMENT_STRUCTURED_CONTEXT_SCHEMA_NAME)
    return merged["data"]


def generate_attachment_structured_context(client, azure_client, model: str, attachment_id: str) -> dict:
    """アップロード済み添付資料の全文からkey_actors/relevant_facts_or_eventsを抽出・統合し、
    geo_ios_session_attachments.structured_contextへ保存する。extraction_status!='ok'または
    extracted_textが無い場合は何もせず添付資料行をそのまま返す（pptx/xlsx等）。
    Azure OpenAIが必要なため、呼び出し元（API層）は既存のsend_attachment_added_turnと同じく
    「失敗してもアップロード自体は既に成功済み」という前提でtry/exceptすること。"""
    rows = client.select("geo_ios_session_attachments", {
        "select": "*", "attachment_id": f"eq.{attachment_id}", "limit": "1"})
    if not rows:
        raise IntakeError(f"attachment_idが見つかりません: {attachment_id}")
    attachment = rows[0]
    if attachment["extraction_status"] != "ok" or not attachment.get("extracted_text"):
        return attachment

    structured = _extract_structured_context(azure_client, model, attachment["extracted_text"])
    client.update("geo_ios_session_attachments", {"attachment_id": f"eq.{attachment_id}"},
                  {"structured_context": structured})
    geo_audit.log_audit(client, actor=attachment["uploaded_by"], action="session_attachment_structured_context",
                         target_table="geo_ios_session_attachments", target_id=attachment_id,
                         detail={"analysis_session_id": attachment["analysis_session_id"],
                                 "chunk_count": len(_chunk_attachment_text(attachment["extracted_text"]))})
    return {**attachment, "structured_context": structured}


class IntakeError(Exception):
    """前提整理Chat/Analysis Context/Session Attachmentの操作エラー。"""


def _require_session_exists(client, analysis_session_id: str) -> None:
    rows = client.select("geo_ios_analysis_sessions", {
        "select": "analysis_session_id", "analysis_session_id": f"eq.{analysis_session_id}", "limit": "1"})
    if not rows:
        raise IntakeError(f"analysis_session_idが見つかりません: {analysis_session_id}")


# ---- Analysis Context（§3、version-append） --------------------------------

def get_current_context(client, analysis_session_id: str) -> dict | None:
    rows = client.select("geo_ios_analysis_contexts", {
        "select": "*", "analysis_session_id": f"eq.{analysis_session_id}",
        "order": "version.desc", "limit": "1"})
    return rows[0] if rows else None


def list_context_history(client, analysis_session_id: str) -> list:
    return client.select("geo_ios_analysis_contexts", {
        "select": "*", "analysis_session_id": f"eq.{analysis_session_id}", "order": "version.asc"})


def create_context_version(client, analysis_session_id: str, *, fields: dict, ready_for_handoff: bool,
                            change_note: str, created_by: str) -> dict:
    """チャットのたびに新versionを追記する（既存create_design_versionと同じ方針）。
    fieldsに含まれないCONTEXT_FIELDSは前versionの値をそのまま引き継ぐ——ユーザーが
    答えていない項目を空へ巻き戻さない。"""
    _require_session_exists(client, analysis_session_id)
    unknown = set(fields) - set(CONTEXT_FIELDS)
    if unknown:
        raise IntakeError(f"未対応のfieldsです: {sorted(unknown)}")

    current = get_current_context(client, analysis_session_id)
    next_version = (current["version"] + 1) if current else 1
    row = {"analysis_session_id": analysis_session_id, "version": next_version,
           "change_note": change_note, "created_by": created_by, "ready_for_handoff": ready_for_handoff}
    if current:
        for field in CONTEXT_FIELDS:
            row[field] = current.get(field)
    row.update(fields)

    created = client.insert("geo_ios_analysis_contexts", [row])[0]
    geo_audit.log_audit(client, actor=created_by, action="analysis_context_new_version",
                         target_table="geo_ios_analysis_contexts", target_id=created["analysis_context_id"],
                         detail={"analysis_session_id": analysis_session_id, "version": next_version})
    return created


# ---- Session Attachment（§4） ------------------------------------------------

def add_attachment(client, analysis_session_id: str, *, filename: str, mime_type: str,
                    raw_bytes: bytes, uploaded_by: str) -> dict:
    _require_session_exists(client, analysis_session_id)
    content_hash = geo_documents_store.compute_content_hash(raw_bytes)
    ext = Path(filename).suffix.lower()

    if ext in _UNSUPPORTED_EXTRACTION_EXTENSIONS:
        extraction_status, extracted_text, extraction_error = "unsupported", None, None
    else:
        extracted = geo_documents_store.extract_text(raw_bytes, filename, mime_type)
        if extracted["ok"]:
            extraction_status, extracted_text, extraction_error = "ok", extracted["text"], None
        else:
            extraction_status, extracted_text, extraction_error = "failed", None, extracted["error"]

    session_dir = INTAKE_UPLOAD_ROOT / analysis_session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    storage_path = session_dir / f"{content_hash}{ext}"
    storage_path.write_bytes(raw_bytes)

    row = {
        "analysis_session_id": analysis_session_id, "filename": filename, "mime_type": mime_type,
        "storage_path": str(storage_path), "content_hash": content_hash,
        "extracted_text": extracted_text, "extraction_status": extraction_status,
        "extraction_error": extraction_error, "uploaded_by": uploaded_by,
    }
    created = client.insert("geo_ios_session_attachments", [row])[0]
    geo_audit.log_audit(client, actor=uploaded_by, action="session_attachment_add",
                         target_table="geo_ios_session_attachments", target_id=created["attachment_id"],
                         detail={"analysis_session_id": analysis_session_id, "filename": filename,
                                 "extraction_status": extraction_status})
    return created


def list_attachments(client, analysis_session_id: str) -> list:
    return client.select("geo_ios_session_attachments", {
        "select": "*", "analysis_session_id": f"eq.{analysis_session_id}", "order": "created_at.asc"})


# ---- Intake Turn（§2-3、同期実行） -------------------------------------------

_TURN_SCHEMA_NAME = "geo_ios_intake_turn"
_TURN_SCHEMA = {
    "type": "object",
    "properties": {
        "reply_message": {"type": "string"},
        "context_updates": {
            "type": "object",
            "properties": {
                "analysis_objective": {"type": "string"},
                "core_question": {"type": "string"},
                "current_situation": {"type": "string"},
                "business_context": {"type": "string"},
                "time_horizon": {"type": "string"},
                "geographic_scope": {"type": "string"},
                "relevant_actors": {"type": "array", "items": {"type": "string"}},
                "key_actors_detail": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "role": {"type": "string"},
                            "controls_or_leverage": {"type": "string"},
                            "key_relationships": {"type": "string"},
                            "constraints": {"type": "string"},
                            "notable_actions": {"type": "string"},
                        },
                        "required": ["name"],
                        "additionalProperties": False,
                    },
                },
                "known_events": {"type": "array", "items": {"type": "string"}},
                "user_assumptions": {"type": "array", "items": {"type": "string"}},
                "must_consider_topics": {"type": "array", "items": {"type": "string"}},
                "internal_context": {"type": "string"},
                "reference_materials": {"type": "array", "items": {"type": "string"}},
                "constraints": {"type": "array", "items": {"type": "string"}},
                "information_gaps": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
        "ready_for_handoff": {"type": "boolean"},
    },
    "required": ["reply_message", "context_updates", "ready_for_handoff"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = """あなたは地政学インテリジェンス分析の「前提整理」を担当するアシスタントです。
担当者(分析依頼者)との会話から、専門家AIへ分析を依頼する前に必要な前提(Analysis Context)を
できるだけ多く、しかし無理なく揃えることが役割です。あなた自身が地政学的な分析・予測を
行うことは絶対にしないでください（それは次の段階の専門家AIの役割です）。

# 揃えたい情報（すべてを無理に埋めない）
- Analysis Objective（analysis_objective）: 何のための分析か
- Core Question（core_question）: 一番知りたいこと
- Current Situation（current_situation）: 今何が起きているかの担当者の理解
- Business / Decision Context（business_context）: どんな判断・業務に使うか
- Time Horizon（time_horizon）: 想定する時間軸
- Geographic Scope（geographic_scope）: 対象地域
- Relevant Actors（relevant_actors）: 担当者が指定した関係者・組織（配列、機関・国家レベルの粒度でよい）
- Key Actors Detail（key_actors_detail）: 会話・添付資料の中に**個人名・具体的な役職**が
  登場した場合、その人物ごとに以下を抽出する配列（担当者に入力させるのではなく、あなたが
  資料本文から拾うこと）:
  - name: 人物名
  - role: 役職・立場
  - controls_or_leverage: その人物が何を動かせるか・持っているレバレッジ
  - key_relationships: 誰との関係が重要か
  - constraints: その人物にかかっている制約（政治的・組織的・状況的）
  - notable_actions: 資料に明示的に書かれている、その人物の**具体的な過去の行動・発言・
    反応**と、その文脈（何がきっかけで、その結果何が起きたか）。例:
    「Aから侮辱的な発言を受けた後、強く反応してB国への攻撃に踏み切った」。
    重要: 資料に書かれている具体的事実だけを保持すること。「〜という性格である」
    「〜すると必ず激発する」のような、一般化された性格・行動法則へあなたが勝手に
    昇格・要約しないこと（それは次段階の専門家AIが複数の事実から判断する仕事であり、
    前提整理チャットの役割ではない）。該当する具体的行動が資料に無ければ空でよい。
  資料に個人名が登場しない場合は無理に埋めない（機関レベルの情報しかなければ
  relevant_actorsだけで十分）。
- Important Events / Facts already known（known_events）: 既知の出来事・事実（配列）
- User Assumptions / Concerns（user_assumptions）: 担当者の仮説・懸念（配列）
- Must-consider Topics（must_consider_topics）: 必ず考慮してほしい論点（配列）
- Internal Context（internal_context）: 社内事情・背景
- Reference Materials（reference_materials）: 添付資料から読み取れる重要ポイントの要約（配列）
- Constraints / Exclusions（constraints）: 除外条件・制約（配列）
- Additional Questions / Information Gaps（information_gaps）: まだ確認したい不足情報（配列）

# 会話の進め方（厳守）
- 一度に大量の質問を並べない。その時点で重要な不足情報を1〜2問だけ聞く。
- 担当者が答えていない項目を無理に埋めようとしない。分析に重要だとあなたが判断した
  不足情報だけを追加で質問する。
- 資料が追加されたら、その資料の内容を踏まえて「この資料を見ると○○が重要そうです。
  今回ここも分析対象に含めますか？」のように反応する。
- context_updatesには、今回の会話で新たに分かった・変わった項目だけを入れる
  （分からない項目のキー自体を省略する。存在しない情報を推測で埋めない）。
- 配列項目（relevant_actors等）は、既に分かっている内容も含めた最新の全体像を返す
  （差分ではなく、その時点での完全なリストを返すこと）。
- 前提がある程度揃ったら（Analysis Objective/Core Question/Current Situation程度が
  揃い、重大なGapが無い状態）、ready_for_handoffをtrueにし、reply_messageで
  「分析に必要な前提は概ね揃いました。この内容で専門家AIに読み方を提案させます。」
  の趣旨を伝える。それ以外はfalseのままにする。
- あなたの返答はreply_message1つのみ。地政学的な分析結果・予測・読み方の提案は
  一切書かない。"""


def _format_key_actor_detail(actor: dict) -> str:
    parts = [actor.get("name") or "?"]
    for field in ("role", "controls_or_leverage", "key_relationships", "constraints", "notable_actions"):
        value = actor.get(field)
        if value:
            parts.append(f"{field}={value}")
    return " / ".join(parts)


def _format_context_for_prompt(context: dict | None) -> str:
    if not context:
        return "（まだ何も整理されていません）"
    lines = []
    for field in CONTEXT_FIELDS:
        value = context.get(field)
        if not value:
            continue
        if field == "key_actors_detail":
            if isinstance(value, list) and value:
                lines.append("- key_actors_detail:")
                for actor in value:
                    lines.append(f"  - {_format_key_actor_detail(actor)}")
        elif field in _CONTEXT_LIST_FIELDS:
            if isinstance(value, list) and value:
                lines.append(f"- {field}: {', '.join(str(v) for v in value)}")
        else:
            lines.append(f"- {field}: {value}")
    return "\n".join(lines) if lines else "（まだ何も整理されていません）"


def _format_turns_for_prompt(turns: list) -> str:
    if not turns:
        return "（まだ会話はありません）"
    lines = []
    for turn in turns:
        lines.append(f"担当者: {turn['user_message']}")
        lines.append(f"あなた: {turn['assistant_message']}")
    return "\n".join(lines)


def _format_attachments_for_prompt(attachments: list) -> str:
    if not attachments:
        return "（添付資料はありません）"
    lines = []
    for a in attachments:
        structured = a.get("structured_context")
        if structured:
            block = [f"# 資料「{a['filename']}」の構造化要約（全文をchunk分割し抽出・統合済み）"]
            actors = structured.get("key_actors") or []
            if actors:
                block.append("人物:")
                for actor in actors:
                    block.append(f"- {_format_key_actor_detail(actor)}")
            facts = structured.get("relevant_facts_or_events") or []
            if facts:
                block.append("重要な事実・出来事:")
                for fact in facts:
                    block.append(f"- {fact}")
            lines.append("\n".join(block))
        elif a["extraction_status"] == "ok" and a.get("extracted_text"):
            # structured_context未生成（Azure未設定・生成失敗等）時のみのfallback。
            # 先頭2000文字のみのため、長い資料では後半の人物・事実が欠落し得ることに注意。
            excerpt = a["extracted_text"][:2000]
            lines.append(f"# 資料「{a['filename']}」の抜粋（構造化要約未生成、先頭2000文字のみ）\n{excerpt}")
        else:
            lines.append(f"# 資料「{a['filename']}」（本文抽出非対応または失敗、ファイル名のみ把握）")
    return "\n\n".join(lines)


def _run_intake_turn(client, azure_client, model: str, analysis_session_id: str, *,
                     trigger: str, user_message: str, attachment_ids: list, created_by: str) -> dict:
    """1往復のIntake Turnを実行する共通処理（send_intake_message/attachment_added_turnの
    両方から呼ぶ）。LLM呼び出し失敗時は例外をそのまま呼び出し元へ伝播し、Turn/Context
    どちらの行も書き込まない（§19 Failure Handlingと同じ既存方針）。"""
    _require_session_exists(client, analysis_session_id)
    current_context = get_current_context(client, analysis_session_id)
    prior_turns = list_turns(client, analysis_session_id)
    attachments = list_attachments(client, analysis_session_id)

    user_prompt = (
        f"# これまでに整理できているAnalysis Context\n{_format_context_for_prompt(current_context)}\n\n"
        f"# これまでの会話\n{_format_turns_for_prompt(prior_turns)}\n\n"
        f"# 添付資料\n{_format_attachments_for_prompt(attachments)}\n\n"
        f"# 担当者の今回のメッセージ\n{user_message}"
    )
    result = geo_llm_common.call_llm_structured(
        azure_client, model, _SYSTEM_PROMPT, user_prompt, _TURN_SCHEMA, _TURN_SCHEMA_NAME)
    data = result["data"]

    context_updates = data.get("context_updates") or {}
    change_note = f"Intake turn: {user_message[:200]}" if context_updates \
        else f"Intake turn（更新なし）: {user_message[:200]}"
    new_context = create_context_version(
        client, analysis_session_id, fields=context_updates, ready_for_handoff=bool(data["ready_for_handoff"]),
        change_note=change_note, created_by=created_by)

    turn_row = {
        "analysis_session_id": analysis_session_id, "trigger": trigger, "user_message": user_message,
        "assistant_message": data["reply_message"], "attachment_ids": attachment_ids,
        "resulting_context_version": new_context["version"], "model": model, "created_by": created_by,
    }
    created_turn = client.insert("geo_ios_intake_turns", [turn_row])[0]
    geo_audit.log_audit(client, actor=created_by, action="intake_turn_create",
                         target_table="geo_ios_intake_turns", target_id=created_turn["intake_turn_id"],
                         detail={"analysis_session_id": analysis_session_id, "trigger": trigger})
    return {"turn": created_turn, "context": new_context}


def send_intake_message(client, azure_client, model: str, analysis_session_id: str, *,
                         user_message: str, created_by: str) -> dict:
    if not (user_message or "").strip():
        raise IntakeError("user_messageが空です")
    return _run_intake_turn(
        client, azure_client, model, analysis_session_id, trigger="user_message",
        user_message=user_message, attachment_ids=[], created_by=created_by)


def send_attachment_added_turn(client, azure_client, model: str, analysis_session_id: str, *,
                                attachment: dict, created_by: str) -> dict:
    """アップロード直後に自動で1往復のTurnを発生させる（§4: 資料が追加されたらAIが
    前提整理に反映し、追加Q&Aできるようにする）。"""
    synthetic_message = f"[資料が追加されました: {attachment['filename']}]"
    return _run_intake_turn(
        client, azure_client, model, analysis_session_id, trigger="attachment_added",
        user_message=synthetic_message, attachment_ids=[attachment["attachment_id"]], created_by=created_by)


def list_turns(client, analysis_session_id: str) -> list:
    return client.select("geo_ios_intake_turns", {
        "select": "*", "analysis_session_id": f"eq.{analysis_session_id}", "order": "created_at.asc"})
