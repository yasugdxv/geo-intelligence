"""ナレッジ項目の「AI暫定承認」: 専門家AIの視点とWeb調査（出典つき）で候補を評価し、条件を満たすものだけを
暫定的に承認する。人の承認（Human Gate #2）とは別の段階で、人があとから確認・修正できる。

背景（ユーザー判断、2026-10-08）: 専門家が足りないから専門家AIを作っている。承認を人だけに頼ると詰まるため、
AIが調べて評価し、承認する。ただし誤りがあってもあとで直せるようにする。

設計上、守っていること（人の関門を壊さない）:
  - 人の承認の関数（geo_knowledge_service.review_knowledge_item）と、人の記録（geo_knowledge_reviews。DBの
    制約で人の権限の行だけ）には触れない。AIの評価は、監査ログ(geo_audit_logs)と、ナレッジ項目の既存の項目
    （approved_by="ai:<モデル名>"、confidence_rationale）に残す。DBの構造は変えない。
  - 承認者に人の名前・権限を使わない。approved_by が "ai:" で始まるものが「AI暫定承認」。画面で見分けられる。
  - 出自の検証（_validate_active_lineage）は、人の承認と同じものを必ず通す。通らない項目は承認しない。
  - 承認する条件は、コードで固定する（AIの「承認します」という言葉だけでは承認しない）:
      verdict=approve かつ 根拠が十分 かつ 確信度が中以上 かつ 事実の誤り/古さ/裏付けなし の問題が無い
      かつ 異なる2つ以上のドメインの出典 かつ 時点が古すぎない。
  - 満たさないものは候補のまま残し、AIの所見（問題点・直し方）だけを記録する（却下はAIがしない）。
  - 解釈中心の種類（アクターのプロファイル等）は、事実の部分と分析・解釈の部分を分けて評価する（案A、ユーザー判断
    2026-10-08）。事実は出典で確認し、解釈は「AIの見立て」とラベルして扱う（専門家の判断も主観や思想に左右される
    ため、誰の解釈かを明示し、専門家のコメントで随時更新する）。解釈は、出典と矛盾しなければ承認の妨げにしない。
  - 「古い」ことでは止めない（ユーザー判断 2026-10-08）。過去の行動・歴史は、人物像や思想を知る上で重要な情報。
    最新情報が未取り込みなことは「更新待ち」の印にとどめ、月次の更新（update_recent_developments）で、
    基本の人物像には触れずに、「最新動向」の部分へ、日付・出典つきで取り込む。
  - 人は、あとから既存の人の承認経路で、非推奨・争いあり等へ変更できる。人が確認したものは confirm_by_human で
    承認者を人に置き換える。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import geo_audit
import geo_knowledge_service as ks
import geo_llm_common
import geo_perplexity_search as pplx

AI_PREFIX = "ai:"
MAX_CONTENT_CHARS = 3500
MAX_SOURCES = 8
RECENT_STALE_DAYS = 45        # 「最新動向」がこれより古い、または未取り込みなら、更新待ちの印にする
BLOCKING_PROBLEM_KINDS = ("factual_error", "outdated", "unsupported")
# 優先して評価する種類（先に、AIの分析の土台になるもの）
TYPE_PRIORITY = ("actor_profile", "institution_profile", "country_foundation", "relationship_assessment",
                 "precedent", "mechanism", "reasoning_rule", "leverage_card")

# 解釈（意図・パターン・立場の分析）を含む種類。事実と解釈を分けて評価する
INTERPRETIVE_TYPES = ("actor_profile", "relationship_assessment", "leverage_card", "mechanism", "reasoning_rule",
                      "historical_path_dependency")
MIN_VERIFIED_FACTS = 3
MIN_VERIFIED_RATIO = 0.6

_INTERPRETIVE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["facts", "interpretation", "outdated", "outdated_note", "confidence", "suggested_fixes", "rationale"],
    "properties": {
        "facts": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["claim", "status", "source_numbers"],
            "properties": {"claim": {"type": "string"},
                           "status": {"type": "string", "enum": ["verified", "contradicted", "unverified"]},
                           "source_numbers": {"type": "array", "items": {"type": "integer"}}}}},
        "interpretation": {"type": "object", "additionalProperties": False,
                           "required": ["assessment", "concerns", "summary"],
                           "properties": {"assessment": {"type": "string", "enum": [
                               "consistent", "partly_consistent", "contradicted", "cannot_judge"]},
                                          "concerns": {"type": "array", "items": {"type": "string"}},
                                          "summary": {"type": "string"}}},
        "outdated": {"type": "boolean"}, "outdated_note": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "suggested_fixes": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
}
_INTERPRETIVE_PROMPT = (
    "あなたは地政学の専門家（アナリスト）で、ナレッジDBの査読者です。下の『ナレッジ項目』は、アクター等の意図・パターン・"
    "立場を分析した**解釈**を含みます。解釈は、出典だけで『正しい』と証明できないのが普通なので、次の2つに分けて評価してください。\n"
    "【1. 事実の部分（facts）】項目の中の、出典で確かめられる事実の主張（日付・人事・出来事・数値・制度・発言など）を、"
    "1つずつ抜き出し、Web調査の結果で判定する。verified=出典で確認できた、contradicted=出典と食い違う、"
    "unverified=確認できなかった。出典に書かれていないことを、あなたの記憶で『確認できた』としない。\n"
    "【2. 分析・解釈の部分（interpretation）】意図・パターン・立場の分析は、事実と矛盾していないか、出典が示す行動と"
    "整合するかを見る。consistent=整合する、partly_consistent=一部整合・言い過ぎの懸念あり、contradicted=事実と"
    "矛盾する、cannot_judge=判断できない。concerns には、言い過ぎ・一般化しすぎ・偏りの懸念を書く。\n"
    "- outdated: 項目の**現在形の記述が、今は誤りになっている**（人物が職を離れた等）場合だけ true にし、outdated_note に"
    "何が変わったかを書く。**時点より後に新しい出来事があっただけ**、**過去の行動・歴史が古い**だけでは true にしない"
    "（古い歴史は人物像・思想を知る重要な情報。最新動向は、別の月次更新で取り込む）。\n"
    "- 専門家のコメントが付いている場合は、事実の誤りの指摘を優先して検証し、解釈については、その見解を考慮する。\n"
    "- confidence: 評価そのものへの確信度。suggested_fixes: 直すべき点。rationale: 判断の理由を2〜3文で。")

_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "evidence_adequacy", "confidence", "verified_points", "problems",
                 "suggested_fixes", "rationale", "cited_source_numbers"],
    "properties": {
        "verdict": {"type": "string", "enum": ["approve", "hold", "reject_suggested"]},
        "evidence_adequacy": {"type": "string", "enum": ["sufficient", "partial", "insufficient"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "verified_points": {"type": "array", "items": {"type": "string"}},
        "problems": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["kind", "text"],
            "properties": {"kind": {"type": "string", "enum": [
                "factual_error", "outdated", "unsupported", "overgeneralization", "bias"]},
                           "text": {"type": "string"}}}},
        "suggested_fixes": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
        "cited_source_numbers": {"type": "array", "items": {"type": "integer"}},
    },
}
_PROMPT = (
    "あなたは地政学の専門家（アナリスト）で、ナレッジDBの査読者です。下の『ナレッジ項目』が、AIの分析の土台として"
    "使ってよいか評価してください。『Web調査の結果』（出典つき）を根拠に、事実としての正しさ・古さ・裏付けの有無を判断する。\n"
    "【評価の観点】\n"
    "- 項目の主張のうち、出典で確認できたものを verified_points に書く（出典番号を添える）。\n"
    "- problems: factual_error=出典と食い違う事実、outdated=現在は成り立たない/古い、unsupported=出典で裏付けが取れない、"
    "overgeneralization=言い過ぎ・一般化しすぎ、bias=特定の立場に偏った書き方。\n"
    "- 出典に書かれていないことを、あなたの記憶で補って『確認できた』としない。確認できなければ unsupported にする。\n"
    "- evidence_adequacy: 主要な主張が出典で確認できれば sufficient、一部なら partial、ほとんど無ければ insufficient。\n"
    "- verdict: approve=使ってよい、hold=人の確認や修正が要る、reject_suggested=誤りが大きく使うべきでない。\n"
    "- confidence: 評価そのものへの確信度。出典が少ない・古い・食い違うなら low。\n"
    "- suggested_fixes: 直すべき点を具体的に。rationale: 判断の理由を2〜3文で。\n"
    "- cited_source_numbers: 根拠にしたWeb調査の出典番号（一覧にある番号だけ）。")


def _tolerant(schema):
    """AIが余計な項目を付けても失敗しないよう、additionalPropertiesを許す（必須項目の検証はそのまま）。"""
    if isinstance(schema, dict):
        return {k: (True if k == "additionalProperties" else _tolerant(v)) for k, v in schema.items()}
    if isinstance(schema, list):
        return [_tolerant(v) for v in schema]
    return schema


_SCHEMA = _tolerant(_SCHEMA)
_INTERPRETIVE_SCHEMA = _tolerant(_INTERPRETIVE_SCHEMA)
FACT_CHUNK = 3                # 事実を、このまとまりごとに別々にWeb検索する
MAX_FACTS = 18
CHUNK_WORKERS = 2
MAX_RECHECKS = 4              # 「誤り」と判定された主張を、単独で再確認する最大数
EVALUATION_RUNS = 2           # 評価のばらつき対策: 2回評価し、両方で承認条件を満たしたものだけ通す


def _flatten(value, limit: int = MAX_CONTENT_CHARS) -> str:
    parts: list = []

    def walk(v, key=None):
        if isinstance(v, str):
            parts.append(f"{key}: {v}" if key else v)
        elif isinstance(v, dict):
            for k, x in v.items():
                if k not in ("entity_id", "evidence_ids"):
                    walk(x, k)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x, key)
    walk(value)
    return " / ".join(p.strip() for p in parts if p and p.strip())[:limit]


def candidate_items(client, *, types: list | None = None, limit: int = 10) -> list:
    """評価対象の候補（candidate / in_review、デモを除く）を、土台になる種類から順に返す。"""
    rows: list = []
    for status in ("candidate", "in_review"):
        rows += client.select("geo_knowledge_items", {
            "select": "*", "status": f"eq.{status}", "is_demo": "eq.false", "limit": "500"})
    if types:
        rows = [r for r in rows if r.get("knowledge_type") in types]
    rank = {t: i for i, t in enumerate(TYPE_PRIORITY)}
    rows.sort(key=lambda r: (rank.get(r.get("knowledge_type"), 99), r.get("title") or ""))
    return rows[:limit]


def web_verify(settings: dict, item: dict) -> dict:
    """項目の主張を、Webで検証する（Perplexity）。戻り値: {"summary", "sources": [{url,title,date}]}。"""
    q = (f"次の記述が、事実として正しいか、現在も成り立つか、信頼できる出典で検証してください。"
         f"対象: {item.get('title')}（{item.get('knowledge_type')}）。記述: {_flatten(item.get('structured_content'), 1800)}")
    r = pplx.search(settings, question=q)
    sources = [{"url": c["url"], "title": c.get("title"), "date": c.get("published_at")}
               for c in r["citations"]][:MAX_SOURCES]
    return {"summary": (r["summary"] or "")[:2500], "sources": sources}


def _domain(url: str) -> str:
    return (urlparse(url or "").netloc or "").lower().removeprefix("www.")


def decide(evaluation: dict, item: dict, sources: list, *, now: datetime | None = None) -> dict:
    """承認してよいかを、コードで決める（AIの言葉だけでは承認しない）。戻り値: {"eligible", "reasons"}。"""
    now = now or datetime.now(timezone.utc)
    reasons: list = []
    if evaluation.get("verdict") != "approve":
        reasons.append(f"AIの判定が approve ではない（{evaluation.get('verdict')}）")
    if evaluation.get("evidence_adequacy") != "sufficient":
        reasons.append(f"根拠が十分ではない（{evaluation.get('evidence_adequacy')}）")
    if evaluation.get("confidence") not in ("high", "medium"):
        reasons.append(f"評価の確信度が低い（{evaluation.get('confidence')}）")
    blocking = [p for p in evaluation.get("problems") or [] if p.get("kind") in BLOCKING_PROBLEM_KINDS]
    if blocking:
        reasons.append("問題あり: " + " / ".join(f"{p['kind']}:{p['text'][:40]}" for p in blocking[:3]))
    cited = {n for n in evaluation.get("cited_source_numbers") or [] if isinstance(n, int) and 1 <= n <= len(sources)}
    domains = {_domain(sources[n - 1]["url"]) for n in cited} - {""}
    if len(domains) < 2:
        reasons.append(f"異なるドメインの出典が2つ未満（{len(domains)}）")
    return {"eligible": not reasons, "reasons": reasons, "cited_domains": sorted(domains),
            "update_needed": update_needed(item, now=now)}


def _comments_block(comments: list | None) -> str:
    if not comments:
        return ""
    lines = [f"- （{c.get('author') or '専門家'}）{c.get('comment')}" for c in comments[:10]]
    return "\n\n# 専門家のコメント（各専門家の見解。主観を含む）\n" + "\n".join(lines)


def decide_interpretive(ev: dict, item: dict, sources: list, *, now: datetime | None = None) -> dict:
    """解釈中心の項目の承認条件（コードで固定）。事実は出典で確かめ、解釈は事実と矛盾しなければ通す。"""
    now = now or datetime.now(timezone.utc)
    reasons: list = []
    facts = ev.get("facts") or []
    verified = [f for f in facts if f.get("status") == "verified"]
    contradicted = [f for f in facts if f.get("status") == "contradicted"]
    if contradicted:
        reasons.append("事実の誤り: " + " / ".join(f["claim"][:45] for f in contradicted[:3]))
    ratio = len(verified) / len(facts) if facts else 0.0
    if len(verified) < MIN_VERIFIED_FACTS or ratio < MIN_VERIFIED_RATIO:
        reasons.append(f"確認できた事実が少ない（{len(verified)}/{len(facts)}件。必要: {MIN_VERIFIED_FACTS}件以上かつ"
                       f"{int(MIN_VERIFIED_RATIO * 100)}%以上）")
    assessment = (ev.get("interpretation") or {}).get("assessment")
    if assessment not in ("consistent", "partly_consistent"):
        reasons.append(f"解釈が事実と整合しない/判断できない（{assessment}）")
    if ev.get("confidence") not in ("high", "medium"):
        reasons.append(f"評価の確信度が低い（{ev.get('confidence')}）")
    nums = {n for f in verified for n in f.get("source_numbers") or [] if isinstance(n, int) and 1 <= n <= len(sources)}
    domains = {_domain(sources[n - 1]["url"]) for n in nums} - {""}
    if len(domains) < 2:
        reasons.append(f"事実を確認した出典が、異なるドメイン2つ未満（{len(domains)}）")
    return {"eligible": not reasons, "reasons": reasons, "cited_domains": sorted(domains),
            "cited_source_numbers": sorted(nums), "update_needed": update_needed(item, now=now),
            "outdated_note": (ev.get("outdated_note") or "") if ev.get("outdated") else ""}


# ---------------------------------------------------------------------------
# 解釈中心の評価（精度向上、2026-10-09）:
#   1. 事実の抜き出し（評価・解釈・項目自体の説明は、事実から除く）
#   2. 事実のまとまりごとに、別々にWeb検索（出典の偏りを減らす）
#   3. 事実ごとの判定（誤りは、出典が明確に反対を述べている場合だけ）
#   4. 「誤り」と判定された主張は、その1件だけで再確認し、確認できたものだけを「誤り」とする
# ---------------------------------------------------------------------------

_FACTS_SCHEMA = {
    "type": "object", "additionalProperties": True, "required": ["facts"],
    "properties": {"facts": {"type": "array", "items": {
        "type": "object", "additionalProperties": True, "required": ["claim"],
        "properties": {"claim": {"type": "string"}}}}},
}
_FACTS_PROMPT = (
    "あなたは地政学の専門家（アナリスト）です。下の『ナレッジ項目』から、**公開された出典で確かめられる事実の主張**だけを、"
    "1つずつ（1文に1つの事実）抜き出してください。最大18件、重要なものから。\n"
    "【事実に含める】日付・就任/退任・出来事・公式な発言や声明・具体的な政策や措置・数値・制度。固有名詞と日付を残す。\n"
    "【事実に含めない（除外する）】\n"
    "- 『〜という評価がある』『〜とされる』『〜の傾向がある』など、評価・解釈・パターンの記述（これは別に扱う）。\n"
    "- 人物の意図・動機・思想の推測。\n"
    "- 『このナレッジ項目は〜を収録している』『Episode N』『needs_enrichment』など、項目自体の構成・内部記録の説明。\n"
    "- 出典で確かめようがない抽象的な一般論。\n"
    "元の項目の言葉を、できるだけ変えずに書く。項目に無いことを足さない。")

_RECHECK_SCHEMA = {
    "type": "object", "additionalProperties": True, "required": ["verdict", "note"],
    "properties": {"verdict": {"type": "string", "enum": ["confirmed_error", "supported", "unclear"]},
                   "note": {"type": "string"}, "source_numbers": {"type": "array", "items": {"type": "integer"}}},
}
_RECHECK_PROMPT = (
    "ある主張が、別の評価で『出典と食い違う（誤り）』と判定されました。下の出典だけを根拠に、この1件を判定し直してください。\n"
    "- confirmed_error: 出典が、この主張と**明確に反対のこと**（別の日付・別の人物・否定）を述べている。\n"
    "- supported: 出典が、この主張を裏付けている（先の判定が誤りだった）。\n"
    "- unclear: 出典に、この主張を裏付ける/否定する記述が見つからない。**確認できないことは、誤りではない**ので unclear にする。\n"
    "出典に書かれていないことを、記憶で補わない。source_numbers には根拠にした出典番号を書く。")


def _chunk(seq: list, n: int) -> list:
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def _verify_chunk(settings: dict, item: dict, claims: list) -> dict:
    q = (f"「{item.get('title')}」に関する、次の事実の主張それぞれが、信頼できる出典で確認できるか、検証してください。"
         "確認できたものは出典を示し、食い違うものはその内容を示してください。\n"
         + "\n".join(f"{i}. {c}" for i, c in enumerate(claims, 1)))
    try:
        r = pplx.search(settings, question=q)
    except pplx.PerplexityError:
        return {"summary": "", "sources": []}
    return {"summary": (r["summary"] or "")[:1500],
            "sources": [{"url": c["url"], "title": c.get("title"), "date": c.get("published_at")} for c in r["citations"]]}


def _merge_sources(chunks: list) -> list:
    out, seen = [], set()
    for ch in chunks:
        for s in ch["sources"]:
            if s["url"] not in seen:
                seen.add(s["url"])
                out.append(s)
    return out[:MAX_SOURCES * 4]


def _recheck_claim(azure_client, model: str, settings: dict, item: dict, claim: str) -> dict:
    """「誤り」と判定された主張を、その1件だけで再検索・再判定する。戻り値: {"verdict","note","sources","source_numbers"}"""
    try:
        r = pplx.search(settings, question=f"「{item.get('title')}」について、次の主張は事実として正しいか、出典で検証してください: {claim}")
    except pplx.PerplexityError:
        return {"verdict": "unclear", "note": "再検索に失敗", "sources": [], "source_numbers": []}
    sources = [{"url": c["url"], "title": c.get("title"), "date": c.get("published_at")} for c in r["citations"]][:MAX_SOURCES]
    src_block = "\n".join(f"[{i}] {x['title'] or x['url']} | {_domain(x['url'])}" for i, x in enumerate(sources, 1)) or "（出典なし）"
    user = f"# 主張\n{claim}\n\n# 検索結果の要約\n{(r['summary'] or '')[:1500]}\n\n# 出典一覧\n{src_block}"
    try:
        res = geo_llm_common.call_llm_structured(
            azure_client, model, _RECHECK_PROMPT, user, _RECHECK_SCHEMA, "knowledge_claim_recheck", max_attempts=2)
    except geo_llm_common.GeoLLMError:
        return {"verdict": "unclear", "note": "再判定に失敗", "sources": sources, "source_numbers": []}
    d = res["data"]
    nums = [n for n in d.get("source_numbers") or [] if isinstance(n, int) and 1 <= n <= len(sources)]
    return {"verdict": d["verdict"], "note": (d.get("note") or "")[:200], "sources": sources, "source_numbers": nums}


def _evaluate_once_interpretive(azure_client, model: str, settings: dict, item: dict, *, now: datetime | None = None,
                                expert_comments: list | None = None) -> dict:
    content = _flatten(item.get("structured_content"))
    head = f"種類: {item.get('knowledge_type')}\nタイトル: {item.get('title')}\n時点: {item.get('as_of_date') or '不明'}\n内容: {content}"
    # 1. 事実の抜き出し（評価・解釈・項目自体の説明は除く）
    fres = geo_llm_common.call_llm_structured(
        azure_client, model, _FACTS_PROMPT, f"# ナレッジ項目\n{head}", _FACTS_SCHEMA, "knowledge_fact_extraction", max_attempts=3)
    claims = [f["claim"].strip() for f in fres["data"].get("facts") or [] if (f.get("claim") or "").strip()][:MAX_FACTS]
    # 2. 事実のまとまりごとに、別々にWeb検索
    groups = _chunk(claims, FACT_CHUNK)
    with ThreadPoolExecutor(max_workers=CHUNK_WORKERS) as ex:
        chunks = list(ex.map(lambda g: _verify_chunk(settings, item, g), groups))
    sources = _merge_sources(chunks)
    src_block = "\n".join(f"[{i}] {x['title'] or x['url']} | {_domain(x['url'])} | {x['date'] or '日付不明'}"
                          for i, x in enumerate(sources, 1)) or "（出典なし）"
    summaries = "\n".join(f"- {c['summary']}" for c in chunks if c["summary"])
    # 3. 事実ごとの判定（誤りは、出典が明確に反対を述べている場合だけ）
    judge_prompt = (_INTERPRETIVE_PROMPT
                    + "\n【この評価での追加ルール】事実の部分（facts）は、下に『抜き出し済みの事実』として与えます。"
                      "それぞれを、そのままの文言で facts に入れ、判定してください（追加・言い換え・削除をしない）。"
                      "contradicted は、出典が**明確に反対のこと**を述べている場合だけ。出典に書かれていない/見つからない場合は、"
                      "誤りではなく unverified にする。")
    user = (f"# ナレッジ項目\n{head}\n\n# 抜き出し済みの事実\n" + "\n".join(f"{i}. {c}" for i, c in enumerate(claims, 1))
            + f"\n\n# Web調査の結果（要約）\n{summaries}\n\n# 出典一覧\n{src_block}{_comments_block(expert_comments)}")
    res = geo_llm_common.call_llm_structured(
        azure_client, model, judge_prompt, user, _INTERPRETIVE_SCHEMA, "knowledge_ai_review", max_attempts=3)
    ev = res["data"]
    # 4. 「誤り」の再確認（その1件だけで、もう一度検索・判定。確認できたものだけを誤りとする）
    rechecks = []
    for f in [f for f in ev.get("facts") or [] if f.get("status") == "contradicted"][:MAX_RECHECKS]:
        rc = _recheck_claim(azure_client, model, settings, item, f["claim"])
        rechecks.append({"claim": f["claim"], "verdict": rc["verdict"], "note": rc["note"]})
        if rc["verdict"] == "confirmed_error":
            continue                                         # 誤りが確定（承認しない）
        if rc["verdict"] == "supported" and rc["source_numbers"]:
            base = len(sources)                              # 再確認の出典を、出典一覧の末尾へ足す
            sources.extend(rc["sources"])
            f["status"], f["source_numbers"] = "verified", [base + n for n in rc["source_numbers"]]
        else:
            f["status"], f["source_numbers"] = "unverified", []   # 確認できない＝誤りではない
    for f in ev.get("facts") or []:
        f["source_numbers"] = [n for n in f.get("source_numbers") or [] if isinstance(n, int) and 1 <= n <= len(sources)]
    verdict = decide_interpretive(ev, item, sources, now=now)
    verdict["confirmed_errors"] = [r["claim"] for r in rechecks if r["verdict"] == "confirmed_error"]
    return {"knowledge_item_id": item["knowledge_item_id"], "title": item.get("title"),
            "knowledge_type": item.get("knowledge_type"), "status": item.get("status"),
            "as_of_date": item.get("as_of_date"), "mode": "interpretive", "evaluation": ev, "sources": sources[:60],
            "extracted_fact_count": len(claims), "rechecks": rechecks, "web_summary": summaries[:600], **verdict}


def _evaluate_once(azure_client, model: str, settings: dict, item: dict, *, now: datetime | None = None,
                   expert_comments: list | None = None) -> dict:
    """1回分の評価（DBへは書かない）。Web調査 → 専門家としての評価 → 承認条件の判定。
    解釈中心の種類は、事実と解釈を分けて評価する（_evaluate_once_interpretive）。専門家のコメントがあれば、材料として渡す。"""
    if item.get("knowledge_type") in INTERPRETIVE_TYPES:
        return _evaluate_once_interpretive(azure_client, model, settings, item, now=now, expert_comments=expert_comments)
    web = web_verify(settings, item)
    src_block = "\n".join(f"[{i}] {s['title'] or s['url']} | {_domain(s['url'])} | {s['date'] or '日付不明'}"
                          for i, s in enumerate(web["sources"], 1)) or "（出典なし）"
    interpretive = item.get("knowledge_type") in INTERPRETIVE_TYPES
    user = (f"# ナレッジ項目\n種類: {item.get('knowledge_type')}\nタイトル: {item.get('title')}\n"
            f"時点: {item.get('as_of_date') or '不明'}\n内容: {_flatten(item.get('structured_content'))}\n\n"
            f"# Web調査の結果（要約。根拠にするのは下の出典一覧の番号）\n{web['summary']}\n\n# 出典一覧\n{src_block}"
            f"{_comments_block(expert_comments)}")
    res = geo_llm_common.call_llm_structured(
        azure_client, model, _INTERPRETIVE_PROMPT if interpretive else _PROMPT, user,
        _INTERPRETIVE_SCHEMA if interpretive else _SCHEMA, "knowledge_ai_review", max_attempts=3)
    ev = res["data"]
    verdict = decide_interpretive(ev, item, web["sources"], now=now) if interpretive else decide(ev, item, web["sources"], now=now)
    return {"knowledge_item_id": item["knowledge_item_id"], "title": item.get("title"),
            "knowledge_type": item.get("knowledge_type"), "status": item.get("status"),
            "as_of_date": item.get("as_of_date"), "mode": "interpretive" if interpretive else "factual",
            "evaluation": ev, "sources": web["sources"], "web_summary": web["summary"][:600], **verdict}


def _run_summary(r: dict) -> dict:
    ev = r.get("evaluation") or {}
    facts = ev.get("facts") or []
    return {"eligible": r.get("eligible", False), "reasons": r.get("reasons", []),
            "facts_verified": sum(1 for f in facts if f.get("status") == "verified"), "facts_total": len(facts),
            "interpretation": (ev.get("interpretation") or {}).get("assessment"), "confidence": ev.get("confidence"),
            "confirmed_errors": r.get("confirmed_errors") or [], "error": r.get("error")}


def _run_with_retry(azure_client, model, settings, item, now, expert_comments) -> dict:
    last_exc: Exception | None = None
    for _attempt in range(2):          # 形式エラー等は、もう1回だけ試す
        try:
            return _evaluate_once(azure_client, model, settings, item, now=now, expert_comments=expert_comments)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    return {"error": str(last_exc)[:120], "eligible": False, "reasons": [f"評価に失敗: {str(last_exc)[:60]}"]}


def evaluate_item(azure_client, model: str, settings: dict, item: dict, *, now: datetime | None = None,
                  expert_comments: list | None = None, runs: int = EVALUATION_RUNS) -> dict:
    """1件を、独立に複数回（既定2回）評価する。AIの評価は、同じ項目でも回ごとにばらつくため（実測: 同じ項目で
    事実の確認 4/15 → 12/12）。判定:
      - 全ての回で承認条件を満たした → eligible
      - 回が分かれた（一部だけ満たす）→ **3回目**を行い、全体の過半数（3回中2回以上）で満たせば eligible
      - ただし、**再確認で確定した事実の誤り**がどの回かにあれば、多数決でも承認しない
      - 全ての回で満たさない → 承認しない
    基準にする評価（出典・事実の判定）は、条件を満たした回のうち最初のもの（無ければ最初の成功した回）。"""
    results = [_run_with_retry(azure_client, model, settings, item, now, expert_comments) for _ in range(max(1, runs))]
    passes = sum(1 for r in results if r.get("eligible"))
    if 0 < passes < len(results):                       # 回が分かれた → 3回目で決める
        results.append(_run_with_retry(azure_client, model, settings, item, now, expert_comments))
        passes = sum(1 for r in results if r.get("eligible"))
    ok = [r for r in results if "error" not in r]
    if not ok:
        raise RuntimeError(results[0]["error"])
    base = next((r for r in ok if r.get("eligible")), ok[0])
    summaries = [_run_summary(r) for r in results]
    confirmed = [e for x in summaries for e in x["confirmed_errors"]]
    needed = len(results) if len(results) <= 2 else (len(results) // 2 + 1)
    eligible = passes >= needed and not confirmed
    out = dict(base)
    out["runs"] = summaries
    out["eligible"] = eligible
    if not eligible:
        reasons = list(base.get("reasons") or []) if not base.get("eligible") else []
        if confirmed:
            reasons.insert(0, "事実の誤りが、再確認で確定した: " + " / ".join(c[:45] for c in confirmed[:3]))
        elif 0 < passes < needed:
            reasons.insert(0, f"評価が回によって分かれた（{passes}/{len(results)}回で条件を満たす。必要: {needed}回）")
        for x in summaries:
            for rs in x["reasons"]:
                if rs not in reasons:
                    reasons.append(rs)
        out["reasons"] = reasons
    else:
        out["reasons"] = []
    return out


def _rationale_text(model: str, result: dict, now: datetime) -> str:
    ev, sources = result["evaluation"], result["sources"]
    head = f"【AI暫定承認 {now.date().isoformat()}・{model}】"
    if result.get("mode") == "interpretive":
        facts = ev.get("facts") or []
        ok = [f["claim"] for f in facts if f.get("status") == "verified"]
        unv = [f["claim"] for f in facts if f.get("status") == "unverified"]
        urls = [sources[n - 1]["url"] for n in result.get("cited_source_numbers") or [] if 1 <= n <= len(sources)]
        interp = ev.get("interpretation") or {}
        text = (f"{head}事実の部分: {len(ok)}/{len(facts)}件を出典で確認（未確認 {len(unv)}件）。"
                f"【分析・解釈の部分はAIの見立て】{interp.get('summary', '')}"
                f"（懸念: {' / '.join((interp.get('concerns') or [])[:3]) or 'なし'}）"
                f" 専門家のコメントで随時更新します。出典: {', '.join(urls)}")
    else:
        cited = {n for n in ev.get("cited_source_numbers") or [] if isinstance(n, int)}
        urls = [sources[n - 1]["url"] for n in sorted(cited) if 1 <= n <= len(sources)]
        text = (f"{head}{ev.get('rationale', '')} 確認できた点: {' / '.join((ev.get('verified_points') or [])[:4])} "
                f"出典: {', '.join(urls)}")
    return text[:1800]


def apply_ai_provisional_approval(client, result: dict, *, model: str, now: datetime | None = None) -> dict:
    """評価結果が承認条件を満たし、かつ出自の検証を通る場合だけ、AI暫定承認にする。
    戻り値: {"applied": bool, "reason": str}。人の承認の関数・記録テーブルには触れない。"""
    now = now or datetime.now(timezone.utc)
    if not result.get("eligible"):
        return {"applied": False, "reason": "承認条件を満たしていない"}
    item = ks.get_knowledge_item(client, result["knowledge_item_id"])
    if not item or item.get("status") not in ("candidate", "in_review"):
        return {"applied": False, "reason": f"状態が候補ではない（{(item or {}).get('status')}）"}
    try:
        ks._validate_active_lineage(client, item)          # 人の承認と同じ、出自の検証
    except ks.LineageValidationError as exc:
        return {"applied": False, "reason": f"出自の検証に通らない: {str(exc)[:80]}"}
    ev, sources = result["evaluation"], result["sources"]
    actor = f"{AI_PREFIX}{model}"
    client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{item['knowledge_item_id']}"}, {
        "status": "active", "retrieval_status": "available", "approved_by": actor, "reviewed_by": actor,
        "version": (item.get("version") or 1) + 1,
        "confidence_rationale": _rationale_text(model, result, now)})
    geo_audit.log_audit(
        client, actor=actor, action="knowledge_item_ai_provisional_approval", target_table="geo_knowledge_items",
        target_id=item["knowledge_item_id"],
        detail={"previous_status": item.get("status"), "new_status": "active", "model": model,
                "mode": result.get("mode"), "evaluation": ev, "cited_domains": result.get("cited_domains"),
                "sources": sources})
    return {"applied": True, "reason": "AI暫定承認"}


def record_hold(client, result: dict, *, model: str) -> None:
    """承認しなかった項目のAI所見を、監査ログへ残す（状態は変えない）。"""
    geo_audit.log_audit(
        client, actor=f"{AI_PREFIX}{model}", action="knowledge_item_ai_review_hold",
        target_table="geo_knowledge_items", target_id=result["knowledge_item_id"],
        detail={"reasons": result.get("reasons"), "evaluation": result.get("evaluation"),
                "sources": result.get("sources")})


def is_ai_approved(item: dict) -> bool:
    return str(item.get("approved_by") or "").startswith(AI_PREFIX)


def confirm_by_human(client, knowledge_item_id: str, *, human_id: str, human_role: str, note: str = "") -> dict:
    """人が、AI暫定承認の内容を確認し、承認者を人に置き換える。人の権限（レビュー担当・管理担当）が必要。"""
    import geo_role_guard
    geo_role_guard.require_role(human_role, ks.REVIEWER_ROLES)
    item = ks.get_knowledge_item(client, knowledge_item_id)
    if not item or not is_ai_approved(item):
        raise ValueError("AI暫定承認の項目ではありません")
    client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{knowledge_item_id}"}, {
        "approved_by": human_id, "reviewed_by": human_id,
        "confidence_rationale": (f"【人が確認済み】{note} （元のAI評価）{item.get('confidence_rationale') or ''}")[:1800]})
    geo_audit.log_audit(
        client, actor=human_id, action="knowledge_item_ai_approval_confirmed_by_human",
        target_table="geo_knowledge_items", target_id=knowledge_item_id,
        detail={"previous_approved_by": item.get("approved_by"), "role": human_role, "note": note})
    return ks.get_knowledge_item(client, knowledge_item_id)


COMMENT_ACTION = "knowledge_item_expert_comment"


def add_expert_comment(client, knowledge_item_id: str, *, author_id: str, author_role: str, comment: str) -> dict:
    """専門家のコメントを、ナレッジ項目に残す（監査ログに保存。DBの構造は変えない）。コメントは、AIの再評価の材料になる。
    項目の内容そのものは、コメントだけでは変わらない（更新は、再評価・人の承認を経る）。"""
    comment = (comment or "").strip()
    if not comment:
        raise ValueError("コメントを入力してください")
    if not ks.get_knowledge_item(client, knowledge_item_id):
        raise ValueError("knowledge_item_idが見つかりません")
    geo_audit.log_audit(client, actor=author_id, action=COMMENT_ACTION, target_table="geo_knowledge_items",
                        target_id=knowledge_item_id, detail={"comment": comment[:2000], "author_role": author_role})
    return {"knowledge_item_id": knowledge_item_id, "author": author_id, "comment": comment[:2000]}


def list_expert_comments(client, knowledge_item_id: str, *, limit: int = 30) -> list:
    rows = client.select("geo_audit_logs", {
        "select": "actor,detail,created_at", "action": f"eq.{COMMENT_ACTION}",
        "target_id": f"eq.{knowledge_item_id}", "order": "created_at.desc", "limit": str(limit)})
    return [{"author": r.get("actor"), "comment": (r.get("detail") or {}).get("comment"),
             "author_role": (r.get("detail") or {}).get("author_role"), "created_at": r.get("created_at")}
            for r in rows if (r.get("detail") or {}).get("comment")]


# ---------------------------------------------------------------------------
# 月次の最新動向の取り込み（基本の人物像には触れず、「最新動向」の部分へ、日付・出典つきで）
# ---------------------------------------------------------------------------

RECENT_KEY = "recent_developments"
RECENT_LABEL = "AIによる月次更新（出典つき）。基本の人物像（歴史・思想）とは別の、直近の動き"

_RECENT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["items", "posture_note", "no_notable_change"],
    "properties": {
        "items": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["date", "event", "bearing", "source_numbers"],
            "properties": {"date": {"type": "string"}, "event": {"type": "string"},
                           "bearing": {"type": "string", "enum": ["confirms_profile", "new_information", "changes_posture"]},
                           "source_numbers": {"type": "array", "items": {"type": "integer"}}}}},
        "posture_note": {"type": "string"},
        "no_notable_change": {"type": "boolean"},
    },
}
_RECENT_PROMPT = (
    "あなたは地政学の専門家（アナリスト）です。ナレッジ項目の人物・主体について、『直近の動きの調査結果』（出典つき）から、"
    "基本プロファイルに照らして意味のある**直近の動き**を、新しい順に最大8件まとめてください。\n"
    "- items: date=出来事の日付（分かる範囲で。YYYY-MM-DD、不明なら『日付不明』）、event=何が起きたか1〜2文（出典に書かれている"
    "ことだけ。推測を足さない）、bearing=confirms_profile（既存の人物像・パターンを裏付ける）/ new_information（新しい情報）/ "
    "changes_posture（既存の姿勢・立場が変わった可能性を示す）、source_numbers=根拠の出典番号（一覧にある番号だけ）。\n"
    "- posture_note: 基本プロファイル（意図・パターン・立場）と照らした**AIの見立て**を2〜3文で。人物像の歴史・思想は変えず、"
    "『現在の姿勢に変化の兆しがあるか』だけを書く。確信が無ければ『変化は確認できない』と書く。\n"
    "- no_notable_change: 注目すべき動きが無ければ true（items は空でよい）。\n"
    "出典に無い出来事を作らない。古い出来事（直近でないもの）は入れない。")


def _parse_date(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)[:10]).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def update_needed(item: dict, *, now: datetime | None = None) -> bool:
    """「最新動向」が未取り込み、または古い（RECENT_STALE_DAYS日超）なら True。解釈中心の種類だけが対象。"""
    if item.get("knowledge_type") not in INTERPRETIVE_TYPES:
        return False
    now = now or datetime.now(timezone.utc)
    rd = (item.get("structured_content") or {}).get(RECENT_KEY) if isinstance(item.get("structured_content"), dict) else None
    d = _parse_date((rd or {}).get("as_of"))
    return d is None or (now - d) > timedelta(days=RECENT_STALE_DAYS)


def fetch_recent_developments(azure_client, model: str, settings: dict, item: dict, *,
                              now: datetime | None = None) -> dict:
    """Perplexityで直近の動きを調べ、「最新動向」の案を作る（DBへは書かない）。"""
    now = now or datetime.now(timezone.utc)
    base = (item.get("structured_content") or {})
    core = _flatten({k: v for k, v in base.items() if k != RECENT_KEY}, 900)
    q = (f"「{item.get('title')}」の直近約1か月（{(now - timedelta(days=31)).date().isoformat()}以降）の動向を、"
         f"発言・行動・人事・政策変更・主な出来事ごとに、日付と出典つきで調べてください。参考（既存の人物像）: {core}")
    r = pplx.search(settings, question=q, period_days=31)
    sources = [{"url": c["url"], "title": c.get("title"), "date": c.get("published_at")} for c in r["citations"]][:MAX_SOURCES]
    src_block = "\n".join(f"[{i}] {s['title'] or s['url']} | {_domain(s['url'])} | {s['date'] or '日付不明'}"
                          for i, s in enumerate(sources, 1)) or "（出典なし）"
    user = (f"# ナレッジ項目\n種類: {item.get('knowledge_type')}\nタイトル: {item.get('title')}\n基本プロファイル: {core}\n\n"
            f"# 直近の動きの調査結果（要約）\n{(r['summary'] or '')[:2500]}\n\n# 出典一覧\n{src_block}")
    res = geo_llm_common.call_llm_structured(
        azure_client, model, _RECENT_PROMPT, user, _RECENT_SCHEMA, "knowledge_recent_update", max_attempts=3)
    data = res["data"]
    items = []
    for e in data.get("items") or []:
        nums = [n for n in e.get("source_numbers") or [] if isinstance(n, int) and 1 <= n <= len(sources)]
        if nums:                    # 出典に結び付かない出来事は載せない
            items.append({"date": e.get("date"), "event": e.get("event"), "bearing": e.get("bearing"),
                          "sources": [sources[n - 1]["url"] for n in nums][:3]})
    return {"knowledge_item_id": item["knowledge_item_id"], "title": item.get("title"),
            "knowledge_type": item.get("knowledge_type"),
            "recent": {"as_of": now.date().isoformat(), "label": RECENT_LABEL, "generated_by": f"{AI_PREFIX}{model}",
                       "items": items, "posture_note": (data.get("posture_note") or "").strip(),
                       "no_notable_change": bool(data.get("no_notable_change")) or not items},
            "sources": sources}


def apply_recent_developments(client, result: dict, *, model: str) -> dict:
    """「最新動向」を、項目の内容へ書き込む。基本の人物像（他の項目）は一切変えない。前の状態は監査ログに残す。
    参照可能（active）な項目だけが対象。"""
    item = ks.get_knowledge_item(client, result["knowledge_item_id"])
    if not item or item.get("status") != "active":
        return {"applied": False, "reason": "承認済み（active）の項目ではない"}
    content = dict(item.get("structured_content") or {})
    previous = content.get(RECENT_KEY)
    content[RECENT_KEY] = result["recent"]
    client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{item['knowledge_item_id']}"},
                  {"structured_content": content})
    geo_audit.log_audit(
        client, actor=f"{AI_PREFIX}{model}", action="knowledge_item_recent_update", target_table="geo_knowledge_items",
        target_id=item["knowledge_item_id"],
        detail={"as_of": result["recent"]["as_of"], "previous": previous, "new": result["recent"],
                "sources": result.get("sources")})
    return {"applied": True, "reason": "最新動向を更新"}


def recent_update_candidates(client, *, types: list | None = None, limit: int = 20, only_stale: bool = True,
                             now: datetime | None = None) -> list:
    """月次更新の対象: 承認済み（active・参照可）の解釈中心の項目。only_staleなら、更新待ちのものだけ。"""
    rows = client.select("geo_knowledge_items", {
        "select": "*", "status": "eq.active", "retrieval_status": "eq.available", "is_demo": "eq.false", "limit": "500"})
    rows = [r for r in rows if r.get("knowledge_type") in (types or INTERPRETIVE_TYPES)]
    if only_stale:
        rows = [r for r in rows if update_needed(r, now=now)]
    return rows[:limit]
