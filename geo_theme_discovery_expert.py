"""テーマ探索の「地政学専門家の視点」層（ユーザー指示、2026-10-08）。

Perplexityなどの検索を単に呼ぶだけでは価値が無いため、専門家AIの資産を探索の入口と出口に使う:
  1. 専門家の分析の型（EXPERT_LENSES）: 問いの作り方・検索の指示・整理の観点を、地政学の専門家の
     切り口（アクターの意図と能力／制度上の制約／過去の類似事例／先行指標／反対の見方／日本企業への影響）
     に揃える。知識DBが空の話題でも常に効く層（コードと指示文のみ）。
  2. 知識DBの裏付け（gather_expert_context）: 探索条件に関係する、承認済みで参照可能な知識項目
     （アクター／制度のプロファイル等）・論点（geo_issues）・エンティティを取り出して渡す。
     該当が無ければ「知識DBの裏付けなし」として明示する（無い知識を作らない）。

守っていること:
  - 参照するのは status=active かつ retrieval_status=available かつ is_demo=false の知識項目だけ
    （候補・制限中・非推奨は使わない。承認ゲートを迂回しない）。
  - 知識は「仮説の材料」であり結論ではない。AIには、知識と矛盾する新しい動きは隠さず明示するよう指示する。
  - 読み取りのみ。DBへは何も書かない。
"""
from __future__ import annotations

import re

# 専門家の分析の型。key=問いの切り口、label=画面表示、search_hint=検索で優先する情報の種類
EXPERT_LENSES = [
    {"key": "actor_intent", "label": "アクターの意図と能力",
     "ask": "主要なアクター（政府・政党・企業・団体）が何を狙い、何ができ/できないか",
     "search_hint": "当事者の発言・声明・人事・資金・動員の具体的な動き"},
    {"key": "institutional_constraint", "label": "制度上の制約",
     "ask": "法律・規則・手続き・日程・司法・予算が、動きをどう縛る/後押しするか",
     "search_hint": "法令・規則案・裁判・選挙管理・予算・日程の一次情報"},
    {"key": "precedent", "label": "過去の類似事例",
     "ask": "過去の似た局面で何が起き、今回はどこが同じ/違うか",
     "search_hint": "過去の事例の分析・調査・シンクタンクの比較研究"},
    {"key": "leading_indicator", "label": "先行指標",
     "ask": "次に起きることを早く知らせる、観測可能な指標は何か",
     "search_hint": "世論調査・資金・登録・出荷・価格・発表日程などの具体的な数値と日付"},
    {"key": "counter_view", "label": "反対の見方",
     "ask": "主流の見立てに反する説明、見落とされがちな要因は何か",
     "search_hint": "異なる立場の専門家・地元メディア・少数意見"},
    {"key": "business_exposure", "label": "日本企業への影響",
     "ask": "日本企業（特に消費財・食品・飲料）のサプライチェーン、規制、市場、レピュテーションへの波及",
     "search_hint": "関税・規制・調達・物流・消費者動向に関する報道・業界資料"},
]

LENS_BY_KEY = {l["key"]: l for l in EXPERT_LENSES}

MAX_KNOWLEDGE_ITEMS = 6
MAX_ISSUES = 5
MAX_CONTEXT_CHARS = 3500
USABLE_STATUS = "active"
USABLE_RETRIEVAL = "available"


def lens_guide_text() -> str:
    """計画・チャットのプロンプトに差し込む、専門家の切り口の一覧。"""
    return "\n".join(f"- {l['label']}（{l['key']}）: {l['ask']}" for l in EXPERT_LENSES)


def search_instruction(lens_key: str | None) -> str:
    """検索AI（Perplexity等）に渡す、切り口ごとの調べ方の指示。"""
    lens = LENS_BY_KEY.get(lens_key or "")
    base = ("地政学の専門家として調べる。単なる報道の要約ではなく、当事者の動機・制度・数値・日付が分かる"
            "具体的な情報源を優先し、一次情報（公的機関・当事者の発表）と、独立した専門家の分析を区別する。"
            "断定せず、情報源が割れている点は両論を示す。")
    if lens:
        base += f"今回の切り口は「{lens['label']}」: {lens['ask']}。優先する情報: {lens['search_hint']}。"
    return base


# ---------------------------------------------------------------------------
# 知識DBの裏付け
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z\-\.]{2,}|[぀-ヿ一-鿿]{2,}")


def _terms(brief: dict) -> list:
    """探索条件から、照合に使う語（キーワード・地域・要約中の語）を作る。"""
    raw = list(brief.get("keywords") or [])
    if brief.get("region"):
        raw.append(brief["region"])
    raw += list(brief.get("actors") or [])
    raw += _TOKEN_RE.findall(brief.get("summary") or "")
    out, seen = [], set()
    for t in raw:
        t = (t or "").strip()
        k = t.lower()
        if len(t) >= 2 and k not in seen:
            seen.add(k)
            out.append(t)
    return out[:30]


def _flatten(value, limit: int = 600) -> str:
    """structured_contentなどを、照合・表示用の短い文章にする。"""
    parts: list = []

    def walk(v):
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    walk(value)
    return " ".join(p.strip() for p in parts if p and p.strip())[:limit]


def _matches(text: str, terms: list) -> list:
    low = (text or "").lower()
    return [t for t in terms if t.lower() in low]


def gather_expert_context(client, brief: dict) -> dict:
    """探索条件に関係する、承認済み・参照可能な知識を取り出す。戻り値:
      {"knowledge": [...], "issues": [...], "entities": [...], "text": 渡すテキスト, "has_support": bool}
    見つからなければ has_support=False・text=""。失敗しても例外にしない（探索を止めない）。"""
    empty = {"knowledge": [], "issues": [], "entities": [], "text": "", "has_support": False}
    terms = _terms(brief)
    if not terms:
        return empty
    try:
        items = client.select("geo_knowledge_items", {
            "select": "knowledge_item_id,knowledge_type,title,structured_content,as_of_date",
            "status": f"eq.{USABLE_STATUS}", "retrieval_status": f"eq.{USABLE_RETRIEVAL}",
            "is_demo": "eq.false", "limit": "300"})
        issues = client.select("geo_issues", {
            "select": "issue_id,name_ja,name_en,description,status", "limit": "300"})
        entities = client.select("geo_entities", {
            "select": "entity_id,canonical_name,name_en,name_ja,local_name,abbreviations", "limit": "300"})
    except Exception:  # noqa: BLE001 — 知識DBが読めなくても、専門家の型だけで探索を続ける
        return empty

    ent_hits = []
    for e in entities:
        names = " ".join(str(e.get(k) or "") for k in ("canonical_name", "name_en", "name_ja", "local_name"))
        names += " " + " ".join(e.get("abbreviations") or [])
        hit = _matches(names, terms)
        if hit:
            ent_hits.append({"entity_id": e["entity_id"], "name": e.get("name_ja") or e.get("canonical_name"),
                             "matched": hit})
    ent_ids = {e["entity_id"] for e in ent_hits}

    know = []
    for it in items:
        sc = it.get("structured_content") or {}
        blob = f"{it.get('title') or ''} {_flatten(sc, 3000)}"
        hit = _matches(blob, terms)
        linked = sc.get("entity_id") in ent_ids if isinstance(sc, dict) else False
        if hit or linked:
            know.append({"item": it, "score": len(hit) + (3 if linked else 0), "matched": hit})
    know.sort(key=lambda x: -x["score"])
    know = know[:MAX_KNOWLEDGE_ITEMS]

    iss = []
    for i in issues:
        if (i.get("status") or "active") not in ("active", "open", None):
            continue
        blob = f"{i.get('name_ja') or ''} {i.get('name_en') or ''} {i.get('description') or ''}"
        hit = _matches(blob, terms)
        if hit:
            iss.append({"issue": i, "score": len(hit)})
    iss.sort(key=lambda x: -x["score"])
    iss = iss[:MAX_ISSUES]

    if not know and not iss:
        return {**empty, "entities": ent_hits}

    lines = ["【知識DBの裏付け（承認済み。仮説の材料であり、結論ではない。今回の動きと矛盾する場合は隠さず明示すること）】"]
    for k in know:
        it = k["item"]
        lines.append(f"- [{it['knowledge_type']}] {it.get('title')}（{it.get('as_of_date') or '日付不明'}時点）: "
                     f"{_flatten(it.get('structured_content'), 420)}")
    for x in iss:
        i = x["issue"]
        lines.append(f"- [論点] {i.get('name_ja') or i.get('name_en')}: {(i.get('description') or '')[:200]}")
    text = "\n".join(lines)[:MAX_CONTEXT_CHARS]
    return {
        "knowledge": [{"knowledge_item_id": k["item"]["knowledge_item_id"], "type": k["item"]["knowledge_type"],
                       "title": k["item"].get("title"), "matched": k["matched"]} for k in know],
        "issues": [{"issue_id": x["issue"]["issue_id"], "name": x["issue"].get("name_ja")} for x in iss],
        "entities": ent_hits, "text": text, "has_support": True}
