"""Web Theme Discovery: Web検索を含む「テーマ」探索（ユーザー指示、2026-10-07）。

従来のArticle Discovery（geo_theme_discovery_service.discover）は「保存済みの記事を集め、記事数が
足りればテーマにする」記事探索だった。本モジュールは、テーマ探索として次の点を変える:

  - Web検索は選んだSourceのdomainに限定しない。ウェブ全体を探す（ソース外の情報も対象）。
    選んだSourceがあれば、そのdomain内の検索も追加で行い、結果に「選択ソース内」の印を付ける。
  - 保存済みの記事（geo_external_signals）も併用する（選んだSourceがあればそのSourceの記事）。
  - 「記事が3件以上・2ドメイン以上」という記事数の足切りをしない。根拠が実在する2件以上の
    異なるURL（Web検索のurl_citation、または保存済み記事）で示せるテーマだけを出す。
  - 日付が取れない根拠は「日付不明」のまま扱う（取得時刻を公開日として見せない）。

守っていること（既存方針の継承）:
  - 候補は自動確定しない。status=NEW の提案（pending）。由来は generation_model の接頭辞
    "web_discovery/" で区別する（DBスキーマ変更なし）。
  - LLMが返す根拠は、渡した一覧の番号に限る。実在しない番号・URLの無い根拠のテーマは捨てる。
  - LLMだけでテーマを作らない。Web検索の要約は参考情報で、根拠にできるのは一覧の項目だけ。
  - 既存のTheme Candidate / Monitoring Theme / Themeと重複するものは新規を乱造せず
    「既存テーマに新しい動き」として返す。削除済み（DISMISSED）は復活させない。
"""
from __future__ import annotations

import copy
import hashlib
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import geo_expert_research_service as expert_research
import geo_external_signal_service as signal_svc
import geo_llm_common
import geo_perplexity_search as pplx
import geo_theme_discovery_expert as expert
import geo_source_research_service as source_research
import geo_theme_discovery_live_service as live
import geo_theme_discovery_service as discovery

ORIGIN = "web_discovery"
VERSION = "web_discovery_v1"
MIN_EVIDENCE_ITEMS = 2     # 1件の根拠だけのテーマは出さない（実在する異なるURLが2件以上）
MAX_ITEMS_TO_LLM = 60
MAX_WEB_ITEMS = 60
MAX_QUERIES = 12           # 1回の探索で行うWeb検索（調べる問い）の最大数
SEARCH_WORKERS = 3         # 同時に実行するWeb検索の数（Perplexityのレート制限に当たりにくくする）
MAX_THEMES = discovery.MAX_THEMES
SEARCH_LENS = "current_news, primary_official, think_tank"

_PROMPT_EXTRA = (
    "\n【この探索の追加ルール】\n"
    "- 一覧にはWeb検索で見つかった項目（日付不明を含む）と保存済み記事が混在します。"
    "日付が不明な項目は、新しさを断定する根拠にしないでください。\n"
    "- 『Web検索の要約』は参考情報です。要約にしか書かれていない内容を根拠にしないでください。"
    "根拠にできるのは[番号]つきの一覧の項目だけです。\n"
    "- 記事の本文は渡していません。タイトルから読み取れる範囲を超えて、事実を足さないでください。")


def _domain_of(url: str) -> str:
    try:
        return (urlparse(url or "").netloc or "").lower()
    except ValueError:
        return ""


# ---------------------------------------------------------------------------
# 1. 収集: Web検索（ソース外を含む）＋保存済み記事
# ---------------------------------------------------------------------------

def _provider_settings() -> dict | None:
    """Perplexityを使う設定（キーあり）を返す。無い、または THEME_SEARCH_PROVIDER=azure ならNone。"""
    import os
    if (os.environ.get("THEME_SEARCH_PROVIDER") or "").strip().lower() == "azure":
        return None
    return pplx.get_settings()


_ASK_MORE = "（異なる情報源を8件以上、できるだけ多様に見つけてください）"


def _search_web(azure_client, model: str, *, question: str, context: str,
                domains: set | None = None, period_days: int | None = None, lens: str | None = None) -> tuple:
    """Web検索を1回行う。戻り値: (citations, 要約, 注記 or None)。
    Perplexityのキーがあればそちらで検索し（公開日つきの引用が返る）、失敗したらAzure OpenAI版へ
    切り替える。domainsを渡した場合は、そのdomain内の結果だけを残す。失敗しても例外にしない。"""
    note = None
    settings = _provider_settings()
    if settings is not None:
        try:
            res = pplx.search(settings, question=question, period_days=period_days,
                              domains=sorted(domains) if domains else None,
                              instruction=expert.search_instruction(lens))
            cites = [{**c, "_via": "perplexity"} for c in res["citations"] if (c.get("url") or "").strip()]
            if domains is not None:
                cites = [c for c in cites if source_research._domain_allowed(_domain_of(c["url"]), domains)]
            return cites, res["summary"], None
        except pplx.PerplexityError as exc:
            note = f"Perplexityでの検索に失敗したため、Azure OpenAI版で検索しました: {str(exc)[:100]}"
    try:
        q = question + _ASK_MORE if domains is None else question
        res = expert_research.run_external_research(
            azure_client, model, question=q, context=context + expert.search_instruction(lens),
            search_lens=SEARCH_LENS)
    except Exception as exc:  # noqa: BLE001 — Web検索の失敗で探索全体を止めない
        return [], "", f"Web検索に失敗しました: {str(exc)[:160]}"
    cites = [{**c, "_via": "azure"} for c in (res.get("citations") or []) if (c.get("url") or "").strip()]
    if domains is not None:
        cites = [c for c in cites if source_research._domain_allowed(_domain_of(c["url"]), domains)]
    return cites, (res.get("summary") or "").strip(), note


_LENS_KEYS = [l["key"] for l in expert.EXPERT_LENSES]
_PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["questions"],
    "properties": {"questions": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["lens", "question"],
        "properties": {"lens": {"type": "string", "enum": _LENS_KEYS}, "question": {"type": "string"}}}}},
}
_PLAN_PROMPT = (
    "あなたは地政学インテリジェンス部門の専門アナリストで、Web調査の設計者です。探索条件から、"
    "Web検索で調べるべき『問い』を10個ほど作ってください。\n"
    "【専門家の切り口】問いは、次の切り口のそれぞれに1〜2個ずつ割り当て、lensに切り口のkeyを入れる:\n"
    + expert.lens_guide_text() + "\n"
    "【ルール】\n"
    "- 単なる『最新ニュース』の問いにしない。当事者の動機、制度・日程、過去の類似事例、観測可能な先行指標、"
    "反対の見方、日本企業への影響を掘る問いにする。\n"
    "- 日本語の問いと英語の問いを半々にする（海外の事柄は英語で検索したほうが情報源が多い）。\n"
    "- 各問いは、そのまま検索できる具体的な一文（固有名詞・地名・期間を含める）。\n"
    "- 『知識DBの裏付け』が渡された場合は、そこに書かれた既知の構図を踏まえ、『それと違う/新しい動き』や"
    "『既知の制約が崩れる兆候』を確かめる問いを作る。知識を前提に決めつけない。\n"
    "- 探索条件に無い国・地域・話題を足さない。\n"
    "- 年を書く場合は、必ず『今日の日付』を基準にする。過去の年（例: 去年や2年前）を、現在の話題として"
    "書かない。『直近』『最新』のような相対的な表現は、年を書かずに使ってよい。")


def plan_queries(azure_client, model: str, brief: dict, now: datetime | None = None,
                 expert_context: dict | None = None) -> list:
    """調べる問いを、専門家の切り口つきでAIに作らせる。戻り値: [{"question", "lens"}]。
    失敗したら、機械的に作った問い（build_web_queries、lens無し）に切り替える。"""
    fallback = [{"question": q, "lens": None} for q in build_web_queries(brief)]
    try:
        today = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
        ctx = (expert_context or {}).get("text") or ""
        user = (f"今日の日付: {today}\n探索条件: {brief.get('summary')}\nキーワード: {'、'.join(brief.get('keywords') or []) or '（なし）'}\n"
                f"地域: {brief.get('region') or '（指定なし）'}\n期間: 直近{brief.get('period_days') or 30}日\n"
                f"{'主要アクター: ' + '、'.join(brief['actors']) + chr(10) if brief.get('actors') else ''}"
                f"{'見たい角度: ' + brief['angle'] + chr(10) if brief.get('angle') else ''}"
                f"{'一般メディアで既に大きく報じられているものは除き、目立たない動きを優先する。' + chr(10) if brief.get('exclude_mainstream') else ''}"
                f"{ctx}")
        res = geo_llm_common.call_llm_structured(
            azure_client, model, _PLAN_PROMPT, user, _PLAN_SCHEMA, "web_theme_discovery_plan", max_attempts=2)
        qs: list = []
        seen: set = set()
        for q in res["data"].get("questions") or []:
            text = ((q or {}).get("question") or "").strip()
            if text and text not in seen:
                seen.add(text)
                qs.append({"question": text, "lens": (q or {}).get("lens")})
        return (qs or fallback)[:MAX_QUERIES]
    except Exception:  # noqa: BLE001 — 計画の失敗で探索全体を止めない
        return fallback


def _norm_queries(queries) -> list:
    """問いの一覧を [{"question", "lens"}] にそろえる（文字列だけの指定も受け付ける）。"""
    return [{"question": q, "lens": None} if isinstance(q, str) else q for q in (queries or [])]


def _query_label(q: dict) -> str:
    lens = expert.LENS_BY_KEY.get(q.get("lens") or "")
    return f"【{lens['label']}】{q['question']}" if lens else q["question"]


def build_web_queries(brief: dict) -> list:
    """探索条件から、切り口の違うWeb検索の質問を作る（最大 MAX_QUERIES 件、重複なし）。
    1回の検索が返す引用は少ないため、要約・キーワード・地域との組み合わせなどで複数回検索する。"""
    keywords = [k for k in (brief.get("keywords") or []) if k]
    summary = (brief.get("summary") or "").strip()
    region = (brief.get("region") or "").strip()
    cands = [summary, " ".join(keywords[:4])]
    if region:
        cands.append(f"{region} " + " ".join(keywords[:3]))
    if len(keywords) >= 2:
        cands += [f"{keywords[0]} {k}" for k in keywords[1:4]]
    cands += [f"{k} 最新 動向" for k in keywords[:2]]
    out: list = []
    for q in cands:
        q = (q or "").strip()
        if q and q not in out:
            out.append(q)
    return out[:MAX_QUERIES]


def collect_web_items(client, azure_client, model: str, *, brief: dict, domains: set,
                      is_demo: bool, now: datetime, queries: list | None = None) -> dict:
    """Webを複数の切り口で検索し、見つかった情報源を geo_external_signals に取り込んで
    「根拠の一覧」にする。戻り値: {"items", "summaries", "stats"}。取り込む記事は
    provider=source_discovery_web（既存のGDELT等とは別provider）。published_at は取得時刻だが
    timestamp_source='unknown' で保存し、画面では日付不明として扱う（item["published_at"] は None）。"""
    keywords = [k for k in (brief.get("keywords") or []) if k]
    summary = (brief.get("summary") or "").strip()
    base_question = summary or " ".join(keywords)
    errors: list = []
    summaries: list = []
    collected: dict = {}   # url -> {"url","title","in_selected_sources"}

    queries = _norm_queries(queries or build_web_queries(brief))
    period_days = int(brief.get("period_days") or discovery.DEFAULT_PERIOD_DAYS)
    via: dict = {}
    context = (f"探索条件: {base_question}。特定のサイトに限定せず、ウェブ全体から、"
               f"直近で『何が変わっているか』が分かる報道・公式発表・分析を探す。")
    with ThreadPoolExecutor(max_workers=min(SEARCH_WORKERS, max(1, len(queries)))) as ex:
        futures = [ex.submit(_search_web, azure_client, model, question=q["question"], context=context,
                             period_days=period_days, lens=q.get("lens")) for q in queries]
        results = [f.result() for f in futures]
    open_n = 0
    for cites, summ, err in results:
        if err and err not in errors:
            errors.append(err)
        if summ:
            summaries.append(summ)
        open_n += len(cites)
        for c in cites:
            via[c.get("_via")] = via.get(c.get("_via"), 0) + 1
            collected.setdefault(c["url"].strip(), {"url": c["url"].strip(), "title": c.get("title"),
                                                    "published_at": c.get("published_at"),
                                                    "in_selected_sources": False})

    src_n = 0
    if domains:
        site_clause = " OR ".join(f"site:{d}" for d in sorted(domains)[:8])
        src_c, src_s, err = _search_web(
            azure_client, model,
            question=base_question if _provider_settings() else f"{base_question} ({site_clause})",
            context=f"探索条件: {base_question}。検索対象は次のdomainに限定: "
                    f"{', '.join(sorted(domains)[:8])}",
            domains=domains, period_days=period_days)
        if err and err not in errors:
            errors.append(err)
        if src_s:
            summaries.append(src_s)
        src_n = len(src_c)
        for c in src_c:
            url = c["url"].strip()
            entry = collected.setdefault(url, {"url": url, "title": c.get("title"),
                                               "published_at": c.get("published_at"), "in_selected_sources": True})
            entry["in_selected_sources"] = True

    normalized, meta_by_key = [], {}
    for entry in list(collected.values())[:MAX_WEB_ITEMS]:
        raw = live._citation_to_raw(entry, now=now)
        if raw is None:
            continue
        try:
            n = live._normalize_web_citation(raw)
        except ValueError:
            continue
        if n["dedup_key"] in meta_by_key:
            continue
        meta_by_key[n["dedup_key"]] = entry
        normalized.append(n)
    upsert = signal_svc.bulk_upsert_signals(client, normalized, provider=live.WEB_PROVIDER, is_demo=is_demo)
    id_by_key = upsert.get("signal_id_by_dedup_key") or {}

    items = []
    for n in normalized:
        sid = id_by_key.get(n["dedup_key"])
        if not sid:
            continue
        entry = meta_by_key[n["dedup_key"]]
        items.append({
            "external_signal_id": sid, "title": entry.get("title") or n["url"], "summary": "",
            "url": n["url"], "source_name": n["source_domain"], "source_domain": n["source_domain"],
            "published_at": entry.get("published_at"),   # 検索結果に公開日があれば使う。無ければ日付不明（取得時刻は見せない）
            "from_web": True, "in_selected_sources": bool(entry.get("in_selected_sources"))})
    stats = {"fetched": len(collected), "inserted": upsert.get("inserted", 0), "updated": upsert.get("updated", 0),
             "open_web": open_n, "selected_sources": src_n, "queries": len(queries), "via": via, "errors": errors}
    return {"items": items, "summaries": summaries, "stats": stats}


def collect_stored_items(client, *, brief: dict, scope: dict, now: datetime) -> list:
    """保存済みの記事から、探索条件に関連するものを集める（選んだSourceがあればそのSourceの記事）。"""
    terms = discovery.build_search_terms(
        {"keywords": brief.get("keywords") or [], "region": brief.get("region")}, {"search_terms": []})
    if not terms:
        return []
    period = max(1, min(int(brief.get("period_days") or discovery.DEFAULT_PERIOD_DAYS), discovery.MAX_PERIOD_DAYS))
    since = (now - timedelta(days=period)).isoformat()
    raw = discovery.retrieve_articles(client, terms, source_ids=scope["source_ids"], domains=scope["domains"],
                                      since_iso=since)
    picked, _ = discovery.select_relevant(raw, terms)
    return picked


def merge_items(web_items: list, stored_items: list) -> list:
    """Web由来と保存済みを1つの一覧にする。同じ記事（ID・同じ見出し）は1件にする。上限は40件。"""
    seen_ids, seen_titles, out = set(), set(), []
    stored_budget = max(0, MAX_ITEMS_TO_LLM - min(len(web_items), MAX_WEB_ITEMS))
    for a in list(web_items) + list(stored_items)[:stored_budget]:
        title_key = discovery.norm_text(a.get("title"))
        if a["external_signal_id"] in seen_ids or (title_key and title_key in seen_titles):
            continue
        seen_ids.add(a["external_signal_id"])
        if title_key:
            seen_titles.add(title_key)
        out.append(a)
        if len(out) >= MAX_ITEMS_TO_LLM:
            break
    return out


# ---------------------------------------------------------------------------
# 2. テーマの整理と検証（記事数の足切りをしない）
# ---------------------------------------------------------------------------

def validate_web_themes(raw_themes: list, items: list) -> list:
    """LLMの出力を検証する。根拠の番号が実在し、実在する異なる情報源が2件以上あるものだけ通す。
    ドメイン数・記事数の下限は設けない（テーマ探索であり、記事数で足切りしない）。"""
    out, used_sets = [], []
    for t in raw_themes[:MAX_THEMES * 2]:
        title = (t.get("title") or "").strip()
        if not title:
            continue
        idx = []
        for n in t.get("evidence_ids") or []:
            if isinstance(n, int) and 1 <= n <= len(items) and n not in idx:
                idx.append(n)
        ev = [items[n - 1] for n in idx]
        ev = [a for a in ev if (a.get("url") or "").strip()]      # URLの無い根拠は使わない
        if len({discovery.norm_text(a.get("title")) or a["external_signal_id"] for a in ev}) < MIN_EVIDENCE_ITEMS:
            continue
        key = frozenset(a["external_signal_id"] for a in ev)
        if any(len(key & u) / min(len(key), len(u)) >= discovery.OVERLAP_DUPLICATE for u in used_sets):
            continue
        used_sets.append(key)
        known = [a["published_at"] for a in ev if a.get("published_at")]
        now_iso = datetime.now(timezone.utc).isoformat()
        out.append({
            "title": title[:120], "short_description": (t.get("short_description") or "").strip()[:500],
            "observed_change": (t.get("observed_change") or "").strip()[:400],
            "why_it_matters": (t.get("why_it_matters") or "").strip()[:500],
            "question": (t.get("question") or "").strip()[:300],
            "actors": [x.strip() for x in (t.get("actors") or []) if x.strip()][:8],
            "regions": [x.strip() for x in (t.get("regions") or []) if x.strip()][:6],
            "topics": [x.strip() for x in (t.get("topics") or []) if x.strip()][:4],
            "candidate_type": t.get("candidate_type") if t.get("candidate_type") in discovery.CANDIDATE_TYPES else "emerging",
            "change_type": t.get("change_type") if t.get("change_type") in discovery.CHANGE_TYPES else "signal_spike",
            "known_context": (t.get("known_context") or "").strip()[:500],
            "leading_indicators": [x.strip() for x in (t.get("leading_indicators") or []) if x.strip()][:3],
            "business_impact": (t.get("business_impact") or "").strip()[:300],
            "contradicts_knowledge": bool(t.get("contradicts_knowledge")),
            "evidence": ev, "evidence_ids": [a["external_signal_id"] for a in ev],
            "first_seen_at": min(known) if known else now_iso,
            "latest_signal_at": max(known) if known else now_iso})
        if len(out) >= MAX_THEMES:
            break
    return out


_EXPERT_THEME_PROPS = {
    "known_context": {"type": "string"},
    "leading_indicators": {"type": "array", "items": {"type": "string"}},
    "business_impact": {"type": "string"},
    "contradicts_knowledge": {"type": "boolean"},
}


def _expert_schema() -> dict:
    schema = copy.deepcopy(discovery._SCHEMA)
    schema["properties"]["themes"]["items"]["properties"].update(_EXPERT_THEME_PROPS)
    return schema


_EXPERT_CLUSTER_ADDENDUM = (
    "\n【地政学の専門家としての追加ルール】\n"
    "- known_context: そのテーマが、過去の類似事例・既知の構図・制度上の制約と照らして『どこが同じで、どこが新しいか』"
    "を1〜2文で書く。根拠の一覧にも、知識DBの裏付けにも無いことは書かない。\n"
    "- leading_indicators: 次に確認すべき、観測可能な先行指標を最大3つ（何を・どこで・いつ確認するか）。\n"
    "- business_impact: 日本企業（特に消費財・食品・飲料）への波及を1文。根拠が薄ければ『現時点では限定的/不明』と書く。\n"
    "- contradicts_knowledge: 『知識DBの裏付け』と矛盾する、または既知の前提を崩す動きならtrue（その場合はknown_contextで明示）。\n"
    "- 知識DBの裏付けは仮説の材料であり結論ではない。裏付けが無い話題では、known_contextに『知識DBの裏付けなし』と書いてよい。")


def cluster_web_themes(azure_client, model, prompt: str, items: list, summaries: list,
                       expert_context: dict | None = None) -> tuple:
    """戻り値: (検証済みテーマ, LLMのno_theme_reason)。"""
    if azure_client is None:
        raise discovery.DiscoveryError("AI分析エンジンが設定されていないため、テーマ候補を整理できません。")
    summary_block = "\n".join(f"- {s}" for s in summaries) or "（なし）"
    ctx = (expert_context or {}).get("text") or "【知識DBの裏付け】該当なし（承認済みの知識項目・論点に、この探索条件と関係するものはありません）"
    user = (f"# 調査プロンプト\n{prompt}\n\n{ctx}\n\n# Web検索の要約（参考。根拠にはしない）\n{summary_block}\n\n"
            f"# 根拠の候補一覧\n{discovery._article_block(items)}")
    try:
        res = geo_llm_common.call_llm_structured(
            azure_client, model, discovery._PROMPT + _PROMPT_EXTRA + _EXPERT_CLUSTER_ADDENDUM, user,
            _expert_schema(), "web_theme_discovery", max_attempts=3)
    except geo_llm_common.GeoLLMError as exc:
        raise discovery.DiscoveryError(
            f"テーマ候補の整理に失敗しました（AIの応答エラー）: {str(exc)[:120]}") from exc
    data = res["data"]
    return validate_web_themes(data.get("themes") or [], items), (data.get("no_theme_reason") or "").strip()


def _row_for_web(theme: dict, model: str | None) -> dict:
    row = discovery._row_for(theme, model)
    row["generation_model"] = f"{ORIGIN}/{model or 'unknown'}"
    extra = []
    if theme.get("known_context"):
        extra.append(("【専門家の見立て（既知の構図との関係）】" + ("※既知の前提と矛盾 " if theme.get("contradicts_knowledge") else "")
                      + theme["known_context"]))
    if theme.get("leading_indicators"):
        extra.append("【次に確認する先行指標】" + " / ".join(theme["leading_indicators"]))
    if extra:
        row["signal_summary"] = (row["signal_summary"] + "\n" + "\n".join(extra))[:1200]
    if theme.get("business_impact"):
        row["business_relevance"] = [theme["business_impact"]]
    row["candidate_fingerprint"] = hashlib.sha256(
        (f"{ORIGIN}:v1:" + ",".join(sorted(theme["evidence_ids"]))).encode()).hexdigest()
    return row


# ---------------------------------------------------------------------------
# 3. 探索の本体
# ---------------------------------------------------------------------------

def discover_web(client, azure_client, model, *, brief: dict, source_ids: list | None = None,
                 progress=None, now: datetime | None = None, is_demo: bool = False) -> dict:
    """戻り値は discover() と同じ形（candidates / existing_updates / message / reason / stats）に、
    web（Web検索の件数とエラー）を加えたもの。progress(step_key) は段階が始まるたびに呼ばれる。"""
    prompt = (brief.get("summary") or "").strip()
    if not prompt:
        raise discovery.DiscoveryError("探索条件が未確定です。チャットで要件をすり合わせてください")
    if azure_client is None:
        raise discovery.DiscoveryError("AI分析エンジンが設定されていないため、テーマ探索を実行できません。")
    now = now or datetime.now(timezone.utc)
    step = progress or (lambda key: None)
    scope = discovery.resolve_scope(client, source_ids, None)
    if scope["mode"] == "selected" and not scope["source_ids"]:
        raise discovery.DiscoveryError("選択された情報ソースが利用できません（アーカイブ済みの可能性があります）。")
    period = max(1, min(int(brief.get("period_days") or discovery.DEFAULT_PERIOD_DAYS), discovery.MAX_PERIOD_DAYS))

    step("interpret")   # 探索条件はチャットで確定済み（ここでは追加のAI呼び出しをしない）
    step("search")
    expert_ctx = expert.gather_expert_context(client, brief)      # 専門家AIの知識（承認済みのみ）
    queries = _norm_queries(plan_queries(azure_client, model, brief, now=now, expert_context=expert_ctx))   # 調べる問いを計画
    web = collect_web_items(client, azure_client, model, brief=brief, domains=scope["domains"],
                            is_demo=is_demo, now=now, queries=queries)   # 問いごとに並列でWeb検索
    stored = collect_stored_items(client, brief=brief, scope=scope, now=now)
    items = merge_items(web["items"], stored)
    web_stats = web["stats"]
    stats = {"articles_considered": len(items), "web_items": len(web["items"]), "stored_items": len(stored),
             "articles_relevant": len(items), "articles_used": len(items)}
    base = {"version": VERSION, "prompt": prompt, "period_days": period,
            "source_scope": {"mode": scope["mode"], "source_count": scope["source_count"]},
            "interpretation": {"keywords": brief.get("keywords"), "region": brief.get("region"),
                               "used_llm": True, "error": None},
            "query_expansion": {}, "stats": stats, "candidates": [], "existing_updates": [],
            "message": None, "from_cache": False, "web": web_stats,
            "searched_topics": [_query_label(q) for q in queries],
            "expert": {"has_support": expert_ctx["has_support"],
                       "knowledge": [{"title": k["title"], "type": k["type"]} for k in expert_ctx["knowledge"]],
                       "issues": [i["name"] for i in expert_ctx["issues"]],
                       "lenses": sorted({q.get("lens") for q in queries if q.get("lens")})}}

    step("cluster")
    if len(items) < MIN_EVIDENCE_ITEMS:
        base["message"] = discovery.NO_RESULT_MESSAGE
        base["reason"] = (f"{len(queries)}個の問いでWeb検索し、{web_stats['fetched']}件の情報源と保存済み記事{len(stored)}件が"
                          "見つかりましたが、テーマの根拠にできる異なる情報源が2件未満でした。")
        return base
    themes, llm_reason = cluster_web_themes(azure_client, model, prompt, items, web["summaries"],
                                            expert_context=expert_ctx)
    existing = discovery.load_existing(client)
    created, updates, skipped_deleted = [], [], 0
    for th in themes:
        dup = discovery.find_duplicate(th["title"], th["evidence_ids"], existing)
        if dup is None:
            row_in = _row_for_web(th, model)
            try:
                created.extend(client.insert("geo_theme_candidates", [row_in]))
            except Exception as exc:  # noqa: BLE001 — fingerprint重複(同時実行)は既存扱いにして続ける
                msg = str(exc)
                if "409" in msg or "duplicate" in msg.lower():
                    continue
                raise
            existing["candidates"].append({**row_in, "candidate_id": created[-1]["candidate_id"]})
        elif dup["type"] == "deleted_candidate":
            skipped_deleted += 1
        else:
            new_articles = [discovery._article_view(a) for a in th["evidence"]
                            if a["external_signal_id"] not in dup["existing_signal_ids"]]
            updates.append({"kind": "existing_update",
                            "existing": {"type": dup["type"], "id": dup["id"], "title": dup["title"],
                                         "status": dup.get("status")},
                            "proposed_title": th["title"], "summary": th["short_description"],
                            "new_articles": new_articles, "has_new_articles": bool(new_articles)})
    stats.update({"themes_proposed": len(themes), "themes_created": len(created),
                  "existing_updates": len(updates), "skipped_deleted": skipped_deleted})
    base["candidates"] = discovery._prepare_for_display(client, created)
    base["existing_updates"] = updates
    if not created and not updates:
        base["message"] = discovery.NO_RESULT_MESSAGE
        base["reason"] = llm_reason or ("調べた情報から『継続して見る価値のある変化』としてまとまるものが"
                                        "ありませんでした。" if not skipped_deleted
                                        else "削除済みのテーマと同じものだけでした。")
    return base


def start_web_job(client, azure_client, model, **params) -> str:
    """discover() のjobと同じ仕組み（同じ _JOBS・段階表示）でWeb探索を非同期に実行する。
    進行状況は既存の GET /api/geo/theme-explorer/discover/{job_id} でそのままポーリングできる。"""
    job_id = uuid.uuid4().hex
    job = {"job_id": job_id, "status": "running", "steps": discovery._new_steps(), "result": None,
           "error": None, "created_at": time.time(), "prompt": (params.get("brief") or {}).get("summary")}
    with discovery._JOBS_LOCK:
        for jid in [j for j, v in discovery._JOBS.items() if time.time() - v["created_at"] > 3600]:
            discovery._JOBS.pop(jid, None)
        discovery._JOBS[job_id] = job

    def run():
        try:
            job["result"] = discover_web(client, azure_client, model,
                                         progress=lambda k: discovery._set_step(job, k), **params)
            now = time.time()
            for s in job["steps"]:
                if s["state"] != "done":
                    s["state"], s["ended_at"] = "done", now
            job["status"] = "done"
        except discovery.DiscoveryError as exc:
            job["status"], job["error"] = "error", str(exc)
        except Exception as exc:  # noqa: BLE001 — 想定外の失敗も画面に理由を返す
            job["status"], job["error"] = "error", f"テーマ探索に失敗しました: {str(exc)[:160]}"

    discovery._EXECUTOR.submit(run)
    return job_id
