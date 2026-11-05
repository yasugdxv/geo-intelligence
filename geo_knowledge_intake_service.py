"""ナレッジ追加（担当者がチャットで依頼 → AIが調査・整理 → AIが登録 → 人が確認）。フェーズ1: Actor のみ。

流れ:
  1. chat(): Expert AI が要件を聞き返し、「要件カード」（名前・種類・視点）を作る。既存との重複も知らせる。
  2. start_job(): 要件カードから、Perplexity（出典つき）で調べ → AIが actor_profile の形に整理 → 登録（candidate）
                  → 既存のAI評価（geo_knowledge_ai_review_service）に通し、条件を満たせば「AI暫定承認」にする。
  3. 人は、既存の confirm_by_human（/api/geo/knowledge/items/{id}/confirm-ai-approval）で「人が確認済み」にできる。

方針:
  - 出典（検索結果のURL）が無い主張は、事実にしない（情報種別を internal_hypothesis にして、信頼度を下げる）。
  - 新しいテーブルは作らない。登録先は既存の geo_entities / geo_knowledge_items。
  - 承認者は "ai:<モデル名>"。人の名前・権限は使わない（既存の AI暫定承認と同じ）。
  - 外部（Perplexity）へ送るのは、要件カードの名前と視点だけ。社内資料は送らない。
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from datetime import datetime, timezone

import geo_entity_service
import geo_knowledge_ai_review_service as ai_review
import geo_knowledge_service as ks
import geo_llm_common
import geo_perplexity_search as pplx

CREATED_BY_PREFIX = "ai:intake:"
ACTOR_ENTITY_TYPES = ("person", "government", "ministry", "political_party", "faction", "military",
                      "security_service", "international_organization", "state_owned_enterprise",
                      "company", "armed_group", "country")
MAX_SOURCES = 8
MAX_CHAT_TURNS = 12

_CHAT_SYSTEM = (
    "あなたは、地政学の専門家AIです。担当者が、Actor（人物・政府・組織など）の知識を、データベースへ追加したい、"
    "と相談してきます。追加に必要な情報（①正式な名前 ②種類 ③どの視点・論点を重視するか）が揃うまで、"
    "不足している点だけを、1回に1〜2問で聞き返してください。分かっている点は、聞き返さないでください。"
    "名前と種類が分かれば、視点が曖昧でも ready にして構いません（視点は空でよい）。"
    "既に分かっているカードの内容を、会話に合わせて更新してください。事実を作らないでください。")

_CHAT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["reply", "ready", "card"],
    "properties": {
        "reply": {"type": "string", "description": "担当者への返答（日本語）。ready なら、要件の確認と調査の開始を促す。"},
        "ready": {"type": "boolean"},
        "card": {
            "type": "object", "additionalProperties": False,
            "required": ["name_en", "name_ja", "entity_type", "country_code", "focus"],
            "properties": {
                "name_en": {"type": ["string", "null"], "description": "英語（正式）名。不明ならnull"},
                "name_ja": {"type": ["string", "null"]},
                "entity_type": {"type": ["string", "null"], "enum": list(ACTOR_ENTITY_TYPES) + [None]},
                "country_code": {"type": ["string", "null"], "description": "ISO 3166-1 alpha-2。不明ならnull"},
                "focus": {"type": ["string", "null"], "description": "重視する視点・論点（例: 対EU政策）。無ければnull"},
            },
        },
    },
}

_PROFILE_SYSTEM = (
    "あなたは、地政学インテリジェンスのアナリストです。与えられた【出典】だけを根拠に、対象Actorの"
    "プロファイルを日本語で作ります。出典に書かれていないことは、書かないでください（推測で補わない）。"
    "各項目は、短い1文にしてください。information_class は、"
    "observed_fact（出典が事実として報じている）/ actor_claim（本人・組織の公式な主張）/ "
    "external_assessment（第三者の評価・分析）から選び、根拠が弱いものは confidence を「低」にしてください。"
    "各項目に、根拠にした出典の番号（source_numbers）を付けてください。番号が付けられない項目は、含めないでください。")

_STATEMENT = {
    "type": "object", "additionalProperties": False,
    "required": ["statement", "information_class", "confidence", "source_numbers"],
    "properties": {
        "statement": {"type": "string"},
        "information_class": {"type": "string", "enum": ["observed_fact", "actor_claim", "external_assessment"]},
        "confidence": {"type": "string", "enum": ["高", "中", "低"]},
        "source_numbers": {"type": "array", "items": {"type": "integer"}},
    },
}
_STATEMENTS = {"type": "array", "items": _STATEMENT}
_PROFILE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["objectives", "core_interests", "red_lines", "constraints", "worldview", "decision_preferences",
                 "coverage_notes", "overall_confidence"],
    "properties": {
        "objectives": _STATEMENTS, "core_interests": _STATEMENTS, "red_lines": _STATEMENTS,
        "constraints": _STATEMENTS, "worldview": _STATEMENTS, "decision_preferences": _STATEMENTS,
        "coverage_notes": {"type": "string", "description": "調べきれなかった点・出典が少ない点"},
        "overall_confidence": {"type": "string", "enum": ["高", "中", "低"]},
    },
}


class IntakeError(Exception):
    pass


# ---- 1. 要件を詰める会話 ----

def _clean_messages(messages: list) -> list:
    out = []
    for m in (messages or [])[-MAX_CHAT_TURNS:]:
        role = "assistant" if m.get("role") == "assistant" else "user"
        text = str(m.get("content") or "").strip()
        if text:
            out.append((role, text[:1500]))
    return out


def _like_term(text: str) -> str:
    return re.sub(r"[,()*%\\]", " ", text or "").strip()


def find_duplicates(client, card: dict) -> list:
    """同じ名前の既存Entityと、そのActor Profile（あれば）を返す。"""
    terms = [t for t in (_like_term(card.get("name_en")), _like_term(card.get("name_ja"))) if len(t) >= 2]
    if not terms:
        return []
    clauses = []
    for t in terms:
        clauses += [f"canonical_name.ilike.*{t}*", f"name_en.ilike.*{t}*", f"name_ja.ilike.*{t}*"]
    ents = client.select("geo_entities", {
        "select": "entity_id,entity_type,canonical_name,name_ja,country_code",
        "or": f"({','.join(clauses)})", "limit": "5"})
    out = []
    for e in ents:
        profs = client.select("geo_knowledge_items", {
            "select": "knowledge_item_id,status,approved_by", "knowledge_type": "eq.actor_profile",
            "structured_content->>entity_id": f"eq.{e['entity_id']}", "limit": "1"})
        p = profs[0] if profs else None
        out.append({**e, "profile_item_id": p["knowledge_item_id"] if p else None,
                    "profile_status": p["status"] if p else None})
    return out


def chat(client, azure_client, model: str, messages: list, card: dict | None = None) -> dict:
    """会話の1ターン。戻り値: {reply, ready, card, duplicates}。"""
    if azure_client is None:
        raise IntakeError("AI分析エンジンが設定されていません")
    turns = _clean_messages(messages)
    if not turns or turns[-1][0] != "user":
        raise IntakeError("メッセージが空です")
    convo = "\n".join(f"{'担当者' if r == 'user' else 'AI'}: {t}" for r, t in turns)
    res = geo_llm_common.call_llm_structured(
        azure_client, model, _CHAT_SYSTEM,
        f"# 現在のカード\n{card or {}}\n# 会話\n{convo}", _CHAT_SCHEMA, "geo_knowledge_intake_chat", max_attempts=2)
    data = res["data"]
    new_card = {k: (data["card"].get(k) or (card or {}).get(k)) for k in
                ("name_en", "name_ja", "entity_type", "country_code", "focus")}
    ready = bool(data["ready"] and (new_card["name_en"] or new_card["name_ja"]) and new_card["entity_type"])
    dups = find_duplicates(client, new_card) if (new_card["name_en"] or new_card["name_ja"]) else []
    reply = data["reply"]
    if dups and ready:
        reply += "\n\n※ 近い名前の既存データがあります（下の「既存」を確認してください）。"
    return {"reply": reply, "ready": ready, "card": new_card, "duplicates": dups}


# ---- 2. 調査 → 整理 → 登録 → AI評価 ----

def _research_question(card: dict) -> str:
    name = " / ".join(x for x in (card.get("name_en"), card.get("name_ja")) if x)
    focus = f"特に「{card['focus']}」の観点を重視して、" if card.get("focus") else ""
    return (f"{name}（{card.get('entity_type')}）について、{focus}"
            "目的・核心的利益・譲れない一線・制約・世界観・意思決定の傾向を、信頼できる出典に基づいて、"
            "事実と評価を区別して説明してください。")


def _sources_block(citations: list) -> list:
    return [{"n": i + 1, "url": c["url"], "title": c.get("title"), "date": (c.get("published_at") or "")[:10] or None}
            for i, c in enumerate(citations[:MAX_SOURCES])]


def _statements_to_content(items: list, sources: list) -> list:
    """出典番号が実在する項目だけ残す。番号の無い・範囲外の項目は捨てる（出典の無い主張を事実にしない）。"""
    valid = {s["n"] for s in sources}
    kept = []
    for it in items or []:
        nums = [n for n in it.get("source_numbers") or [] if n in valid]
        text = (it.get("statement") or "").strip()
        if not nums or not text:
            continue
        kept.append({"statement": text, "information_class": it["information_class"], "confidence": it["confidence"],
                     "observed_at": None, "last_reviewed_at": None, "reviewed_by": None,
                     "evidence_ids": [next(s["url"] for s in sources if s["n"] == n) for n in nums]})
    return kept


def build_profile(azure_client, model: str, card: dict, research: dict, sources: list) -> dict:
    src_text = "\n".join(f"[{s['n']}] {s['title'] or ''} {s['url']} ({s['date'] or '日付不明'})" for s in sources)
    res = geo_llm_common.call_llm_structured(
        azure_client, model, _PROFILE_SYSTEM,
        f"# 対象\n{card.get('name_en') or ''} / {card.get('name_ja') or ''}（{card.get('entity_type')}）\n"
        f"# 重視する視点\n{card.get('focus') or '（指定なし）'}\n"
        f"# 検索AIによる要約（参考。個別の主張の出典にはしない）\n{(research.get('summary') or '')[:3000]}\n"
        f"# 出典\n{src_text}", _PROFILE_SCHEMA, "geo_knowledge_intake_profile", max_attempts=2)
    return res["data"]


def find_or_create_entity(client, card: dict, actor_id: str) -> tuple[dict, bool]:
    """既存Entityがあれば使う（名前が完全一致するもの）。無ければ作る。戻り値: (entity, 新規か)。"""
    for e in find_duplicates(client, card):
        names = {(e.get("canonical_name") or "").lower(), (e.get("name_ja") or "").lower()}
        if (card.get("name_en") or "").lower() in names or (card.get("name_ja") or "").lower() in names:
            return e, False
    created = geo_entity_service.create_entity(
        client, entity_type=card["entity_type"], canonical_name=card.get("name_en") or card["name_ja"],
        actor_id=actor_id, name_en=card.get("name_en"), name_ja=card.get("name_ja"),
        country_code=card.get("country_code"))
    return created, True


def register_candidate(client, azure_client, model: str, card: dict, requester_id: str, *, settings: dict,
                       progress=lambda stage: None) -> dict:
    """調査 → 整理 → 登録（candidate）。戻り値: {item, entity, entity_created, sources}。"""
    progress("調べています（Web検索）")
    try:
        research = pplx.search(settings, question=_research_question(card))
    except pplx.PerplexityError as exc:
        raise IntakeError(f"Web検索に失敗しました（{str(exc)[:80]}）") from exc
    sources = _sources_block(research["citations"])
    if not sources:
        raise IntakeError("出典となるページが見つかりませんでした。名前や視点を変えて、もう一度お試しください")
    progress("整理しています")
    prof = build_profile(azure_client, model, card, research, sources)
    objectives = _statements_to_content(prof["objectives"], sources)
    interests = _statements_to_content(prof["core_interests"], sources)
    if not objectives and not interests:
        raise IntakeError("出典に基づく内容を作れませんでした。視点を変えて、もう一度お試しください")
    progress("登録しています")
    actor = f"{CREATED_BY_PREFIX}{requester_id}"
    entity, created = find_or_create_entity(client, card, actor)
    cp = {"worldview": _statements_to_content(prof["worldview"], sources),
          "core_objectives": objectives,
          "decision_preferences": _statements_to_content(prof["decision_preferences"], sources),
          "red_lines": _statements_to_content(prof["red_lines"], sources),
          "preferred_instruments": [],
          "structural_constraints": _statements_to_content(prof["constraints"], sources)}
    content = {
        "entity_id": entity["entity_id"],
        "objectives": [s["statement"] for s in objectives],
        "core_interests": [s["statement"] for s in interests],
        "red_lines": [s["statement"] for s in cp["red_lines"]],
        "constraints": [s["statement"] for s in cp["structural_constraints"]],
        "veto_power": None, "core_profile": cp,
        "coverage_notes": (prof.get("coverage_notes") or "")[:600] + "（ナレッジ追加: AIがWeb検索の出典から作成）",
        "needs_enrichment": ["current_posture未整備", "issue_positions未整備"],
        "intake": {"requested_by": requester_id, "focus": card.get("focus"), "sources": sources,
                   "created_at": datetime.now(timezone.utc).isoformat()},
    }
    title = card.get("name_en") or card["name_ja"]
    if card.get("name_ja") and card.get("name_en"):
        title = f"{card['name_en']} ({card['name_ja']})"
    item = ks.create_knowledge_item(
        client, knowledge_type="actor_profile", title=title, structured_content=content,
        information_class="external_assessment", created_by=actor,
        summary=(prof.get("coverage_notes") or "")[:300] or None,
        confidence=prof["overall_confidence"], confidence_rationale="ナレッジ追加（AIがWeb検索の出典から作成。AI評価前）")  # origin は制約があるためNULL。created_by の接頭辞で見分ける
    return {"item": item, "entity": entity, "entity_created": created, "sources": sources}


def review_candidate(client, azure_client, model: str, item: dict, *, settings: dict) -> dict:
    """既存のAI評価に通す。戻り値: {approved(bool), reason, reasons}。"""
    fresh = ks.get_knowledge_item(client, item["knowledge_item_id"])
    result = ai_review.evaluate_item(azure_client, model, settings, fresh)
    if result.get("eligible"):
        out = ai_review.apply_ai_provisional_approval(client, result, model=model)
        if out["applied"]:
            return {"approved": True, "reason": "AI暫定承認", "reasons": []}
        ai_review.record_hold(client, result, model=model)
        return {"approved": False, "reason": out["reason"], "reasons": []}
    ai_review.record_hold(client, result, model=model)
    return {"approved": False, "reason": "AI評価の承認条件を満たさなかったため、候補のままです",
            "reasons": [str(r)[:160] for r in (result.get("reasons") or [])[:4]]}


# ---- ジョブ（調査は1〜2分かかるため、画面は状況を問い合わせる）----

_JOBS: dict = {}
_LOCK = threading.Lock()
_MAX_JOBS = 50


def _set(job_id: str, **kw) -> None:
    with _LOCK:
        _JOBS[job_id].update(kw, updated_at=time.time())


def get_job(job_id: str, requester_id: str) -> dict | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job and job["requester_id"] == requester_id else None


def start_job(client, azure_client, model: str, card: dict, requester_id: str) -> str:
    if azure_client is None:
        raise IntakeError("AI分析エンジンが設定されていません")
    if card.get("entity_type") not in ACTOR_ENTITY_TYPES or not (card.get("name_en") or card.get("name_ja")):
        raise IntakeError("名前と種類（Actorの種類）が必要です")
    settings = pplx.get_settings()
    if settings is None:
        raise IntakeError("Web検索のAPIキーが設定されていません")
    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        if len(_JOBS) >= _MAX_JOBS:
            for k in sorted(_JOBS, key=lambda k: _JOBS[k]["updated_at"])[:10]:
                _JOBS.pop(k, None)
        _JOBS[job_id] = {"job_id": job_id, "requester_id": requester_id, "status": "running",
                         "stage": "開始しています", "card": card, "result": None, "error": None,
                         "updated_at": time.time()}

    def run():
        try:
            reg = register_candidate(client, azure_client, model, card, requester_id, settings=settings,
                                     progress=lambda s: _set(job_id, stage=s))
            item = reg["item"]
            _set(job_id, stage="登録しました。AIが内容を検証しています（1〜2分）",
                 result={"knowledge_item_id": item["knowledge_item_id"], "title": item["title"],
                         "entity_created": reg["entity_created"], "sources": reg["sources"], "review": None})
            try:
                review = review_candidate(client, azure_client, model, item, settings=settings)
            except Exception as exc:  # noqa: BLE001 — 検証に失敗しても、登録（候補）は残る
                review = {"approved": False, "reason": f"AI評価に失敗しました（{type(exc).__name__}）。候補のままです",
                          "reasons": []}
            with _LOCK:
                _JOBS[job_id]["result"]["review"] = review
            _set(job_id, status="done", stage="完了")
        except IntakeError as exc:
            _set(job_id, status="error", error=str(exc), stage="失敗")
        except Exception as exc:  # noqa: BLE001 — 本文・検索語はエラーに含めない
            _set(job_id, status="error", error=f"処理に失敗しました（{type(exc).__name__}）", stage="失敗")

    threading.Thread(target=run, name=f"knowledge-intake-{job_id}", daemon=True).start()
    return job_id


# ---- 一覧 ----

def list_intake_items(client, *, limit: int = 50) -> list:
    rows = client.select("geo_knowledge_items", {
        "select": "knowledge_item_id,title,status,approved_by,created_by,confidence,summary,created_at,structured_content",
        "created_by": f"like.{CREATED_BY_PREFIX}*", "order": "created_at.desc", "limit": str(limit)})
    out = []
    for r in rows:
        approved_by = r.get("approved_by") or ""
        content = r.get("structured_content") or {}
        out.append({
            "knowledge_item_id": r["knowledge_item_id"], "title": r["title"], "status": r["status"],
            "confidence": r.get("confidence"), "created_at": r.get("created_at"), "created_by": r.get("created_by"),
            "approval": ("human" if approved_by and not approved_by.startswith(ai_review.AI_PREFIX)
                         else "ai" if approved_by else "none"),
            "approved_by": approved_by or None,
            "objectives": (content.get("objectives") or [])[:3], "core_interests": (content.get("core_interests") or [])[:3],
            "sources": ((content.get("intake") or {}).get("sources") or [])[:MAX_SOURCES],
            "rationale": None})
    return out
