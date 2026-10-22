"""Intelligence AI プリセット: データソース・アダプター。

各アダプターは「実際に接続して検索できたか」を status で正直に返す:
    searched   : 検索を実行した（0件でもこの扱い）
    no_access  : 権限がなく検索しなかった（結果に混ぜない）
    unavailable: 接続できない（テーブル未作成・キー未設定など）
    error      : 実行中に失敗した
    skipped    : 条件により対象外（ユーザーが選ばなかった等）
未接続・失敗のソースを「検索した」ように扱わない。

権限: レポート/ナレッジは既存の Permission→Eligible Corpus→Retrieval（geo_intelligence_ai_search_service）を
そのまま使い、外部コンタクトは geo_external_network_service の可視性ルール（作成者/参加者/管理者のみ）に従う。
Web検索には「調査テーマの文言」だけを送る（社内資料・面談メモの内容は送らない）。

正規化した Evidence の形:
    {source_type, id, title, date, url, snippet, origin("internal"|"web"), meta}
"""
from __future__ import annotations

import re

import geo_external_network_service as ext_net
import geo_intelligence_ai_search_service as search_service
import geo_knowledge_access_policy as access_policy
import geo_perplexity_search as pplx

SNIPPET_CHARS = 500
DEFAULT_LIMIT = 8

# 専門家AIの Foundation / 事例系ナレッジ（apps内の「共通知識基盤」）。approved_report は search_service 側が担当
KNOWLEDGE_TYPES_BY_GROUP = {
    "cases": ["precedent", "historical_path_dependency", "case_finding", "mechanism"],
    "views": ["case_finding", "relationship_assessment", "reasoning_rule"],
    "general": ["country_foundation", "actor_profile", "relationship_assessment", "precedent",
                "historical_path_dependency", "case_finding", "mechanism", "reasoning_rule"],
}


def _trim(text, n: int = SNIPPET_CHARS) -> str | None:
    t = " ".join(str(text or "").split())
    return (t[:n] + "…") if len(t) > n else (t or None)


def _result(status: str, reason: str | None = None, items: list | None = None) -> dict:
    return {"status": status, "reason": reason, "items": items or []}


_SPLIT = re.compile(r"[\s、。,，/・()（）「」:：;；\-–—]+")


def tokens(terms: list, limit: int = 10) -> list:
    """検索語を、1語ずつの単位に分ける（「中国 経済圧力 台湾」のようなフレーズを丸ごと部分一致させると、
    実データではほぼ0件になるため）。短すぎる語（英数字3文字未満・日本語2文字未満）は除く。"""
    out, seen = [], set()
    for term in terms:
        for w in _SPLIT.split(term or ""):
            w = w.strip()
            if len(w) < (3 if w.isascii() else 2) or w.lower() in seen:
                continue
            seen.add(w.lower())
            out.append(w)
    return out[:limit]


def _score(text: str, toks: list) -> int:
    low = (text or "").lower()
    return sum(1 for t in toks if t.lower() in low)


def _min_score(toks: list) -> int:
    """語が多い検索では、1語だけ一致した無関係な記事（「中国」だけ等）を除く。"""
    return 2 if len(toks) >= 4 else 1


def _relative_min(best_hits: int, need: int) -> int:
    """最も多く一致した資料の語数が need 以上なら need、そうでなければ最小（=全て残す）。"""
    return need if best_hits >= need else 1


def _toks(ctx: dict) -> list:
    return ctx.get("tokens") or tokens(ctx["terms"]) or [t for t in ctx["terms"] if t]


def _or_ilike(columns: tuple, terms: list) -> str:
    """PostgRESTの or=(col.ilike.*語*,...)。ワイルドカード・区切り文字になる記号は語から除く。"""
    parts = []
    for term in terms:
        safe = "".join(ch for ch in term if ch not in "*(),%\\\"'")[:40].strip()
        if safe:
            parts.extend(f"{c}.ilike.*{safe}*" for c in columns)
    return "(" + ",".join(parts) + ")" if parts else ""


# ---- レポート + 承認済みナレッジ（既存の権限付き検索） ----
def search_reports(ctx: dict) -> dict:
    filters = {}
    if ctx.get("date_from"):
        filters["date_from"] = ctx["date_from"]
    if ctx.get("region"):
        filters["region"] = ctx["region"]
    searched = _toks(ctx)[:4]
    by_key: dict = {}
    for term in searched:                                 # 全文検索はAND一致のため、語ごとに検索して統合する
        res = search_service.search(
            ctx["client"], term, requester_context=ctx["requester_context"], include_demo=False,
            limit=DEFAULT_LIMIT, filters=filters or None)
        for r in res["results"]:
            key = (r["source_type"], r.get("report_id") or r.get("knowledge_item_id"))
            entry = by_key.setdefault(key, {"hits": 0, "row": r})
            entry["hits"] += 1                            # いくつの語に一致したか
            if (r.get("text_score") or 0) > (entry["row"].get("text_score") or 0):
                entry["row"] = r
    # 強く一致する資料（複数語に一致）がある場合だけ、1語しか一致しない弱い資料を除く。
    # 強い一致が1つも無い場合は、全て残す（日本語の資料は、英語の検索語に当たらないため、語数だけで切ると全滅する）
    need = _relative_min(max((e["hits"] for e in by_key.values()), default=0), _min_score(searched))
    items = []
    for key, e in by_key.items():
        if e["hits"] < need:
            continue
        r = e["row"]
        items.append({
            "source_type": r["source_type"], "id": key[1], "origin": "internal",
            "title": r.get("title"), "date": (r.get("date") or "")[:10] or None, "url": None,
            "snippet": _trim(r.get("snippet") or r.get("summary")),
            "meta": {"review_status": r.get("review_status"), "region": r.get("region"),
                     "author": r.get("author"), "confidentiality": r.get("confidentiality_label"),
                     "_hits": e["hits"], "_score": r.get("text_score") or 0.0}})
    items.sort(key=lambda e: (e["meta"]["_hits"], e["meta"]["_score"]), reverse=True)
    return _result("searched", None, items[:DEFAULT_LIMIT * 2])


# ---- 共通知識基盤（専門家AIが使う geo_knowledge_items） ----
def search_knowledge(ctx: dict) -> dict:
    group = ctx.get("knowledge_group", "general")
    types = KNOWLEDGE_TYPES_BY_GROUP[group]
    client, rc = ctx["client"], ctx["requester_context"]
    eligible: set = set()
    for ktype in types:
        eligible |= search_service.resolve_eligible_knowledge_item_ids(
            client, requester_context=rc, include_demo=False, knowledge_type=ktype)
    if not eligible:
        return _result("searched", "権限内で検索できる共通ナレッジがありません")
    hits: dict = {}
    counts: dict = {}
    searched = _toks(ctx)[:4]
    for term in searched:
        rows = client.rpc("search_geo_knowledge_items", {
            "p_query": term, "p_status": ["active"], "p_knowledge_types": types, "p_include_demo": False,
            "p_limit": DEFAULT_LIMIT, "p_eligible_ids": sorted(eligible)}) or []
        for h in rows:
            if h.get("knowledge_item_id") in eligible:            # 二重チェック（既存検索と同じ防御）
                hits.setdefault(h["knowledge_item_id"], h)
                counts[h["knowledge_item_id"]] = counts.get(h["knowledge_item_id"], 0) + 1
    need = _relative_min(max(counts.values(), default=0), _min_score(searched))     # 強い一致がある場合だけ、弱い一致を除く
    hits = {k: v for k, v in hits.items() if counts.get(k, 0) >= need}
    if not hits:
        return _result("searched", None, [])
    rows = client.select("geo_knowledge_items", {
        "select": "*", "knowledge_item_id": f"in.({','.join(sorted(hits))})"})
    items = []
    for r in rows:
        sc = r.get("structured_content") or {}
        date = (sc.get("as_of_date") or r.get("as_of_date") or r.get("created_at") or "")[:10] or None
        if ctx.get("date_from") and date and date < ctx["date_from"]:
            continue
        hit = hits[r["knowledge_item_id"]]
        items.append({
            "source_type": "knowledge_item", "id": r["knowledge_item_id"], "origin": "internal",
            "title": r.get("title"), "date": date, "url": None,
            "snippet": _trim(hit.get("snippet") or r.get("summary")),
            "meta": {"knowledge_type": r.get("knowledge_type"), "information_class": r.get("information_class"),
                     "ai_provisional": str(r.get("approved_by") or "").startswith("ai:"),
                     "_score": hit.get("text_score") or 0.0}})
    items.sort(key=lambda e: e["meta"]["_score"], reverse=True)
    return _result("searched", None, items[:DEFAULT_LIMIT * 2])


# ---- 記事DB（収集済みの外部シグナル記事） ----
def search_articles(ctx: dict) -> dict:
    toks = _toks(ctx)
    or_expr = _or_ilike(("title", "summary"), toks)
    if not or_expr:
        return _result("searched", None, [])
    # 語のどれかに一致する新しい記事を多めに取り、一致した語の数が多い順に並べる（同数なら新しい順）
    cols = ("external_signal_id,title,summary,url,source_name,source_domain,source_country,published_at,"
            "evidence_status,provider")
    params = {"select": cols + ",source_id", "is_demo": "eq.false", "or": or_expr, "order": "published_at.desc", "limit": "120"}
    if ctx.get("date_from"):
        params["published_at"] = f"gte.{ctx['date_from']}"
    try:
        rows = ctx["client"].select("geo_external_signals", params)
    except Exception:  # noqa: BLE001 — source_id列が無い環境（Source Library DB-3 未適用）でも、記事検索は動かす
        rows = ctx["client"].select("geo_external_signals", {**params, "select": cols})
    scored = [(_score(f"{r.get('title') or ''} {r.get('summary') or ''}", toks), r) for r in rows]
    need = _relative_min(max((s for s, _ in scored), default=0), _min_score(toks))    # 強い一致がある場合だけ、弱い一致を除く
    scored = [(s, r) for s, r in scored if s >= need]
    scored.sort(key=lambda sr: (sr[0], sr[1].get("published_at") or ""), reverse=True)
    top = [r for _, r in scored[:DEFAULT_LIMIT * 2]]
    profiles = _source_profiles(ctx["client"], [r["source_id"] for r in top if r.get("source_id")])
    items = [{
        "source_type": "article", "id": r["external_signal_id"], "origin": "internal",
        "title": r.get("title"), "date": (r.get("published_at") or "")[:10] or None, "url": r.get("url"),
        "snippet": _trim(r.get("summary")),
        "meta": {"source_name": r.get("source_name") or r.get("source_domain"), "country": r.get("source_country"),
                 "evidence_status": r.get("evidence_status"), "provider": r.get("provider"),
                 "source_profile": profiles.get(r.get("source_id"))}} for r in top]
    if ctx.get("fetch_bodies", True):
        _attach_bodies(ctx, items, toks)
    return _result("searched", None, items)


def _source_profiles(client, source_ids: list) -> dict:
    """記事の出どころ（Source Libraryの登録）。記事に source_id が付いているものだけ。登録の評価（一次情報か・媒体・信頼性の注記）を添える。"""
    ids = sorted({i for i in source_ids if i})
    if not ids:
        return {}
    try:
        rows = client.select("geo_source_registry", {
            "select": "source_id,name,media_type,is_primary,reliability", "source_id": "in.(" + ",".join(ids) + ")"})
    except Exception:  # noqa: BLE001
        return {}
    return {r["source_id"]: {k: r.get(k) for k in ("name", "media_type", "is_primary", "reliability") if r.get(k) not in (None, "")}
            for r in rows}


BODY_TOP = 4        # 本文を取得する記事の数（一致度の高い上位のみ）


def _attach_bodies(ctx: dict, items: list, toks: list) -> None:
    """上位の記事だけ、URLから本文を取得（キャッシュあり）して、根拠の抜粋を本文のものに置き換える。
    取得できなかった記事は、要約のままにして、その旨（body_status）を残す。"""
    import geo_article_body_service as bodies
    cands = [{"id": i["id"], "url": i["url"]} for i in items[:BODY_TOP] if i.get("url")]
    if not cands:
        return
    try:
        got = bodies.ensure_bodies(ctx["client"], cands)
    except Exception:  # noqa: BLE001 — 本文が取れなくても、要約での検索結果は返す
        return
    for i in items[:BODY_TOP]:
        g = got.get(i["id"])
        if not g:
            continue
        i["meta"]["body_status"] = g["status"]
        if g["status"] == "ok" and g.get("body_text"):
            i["snippet"] = bodies.best_passage(g["body_text"], toks, 1200)


# ---- モニタリングDB（アラート・テーマの最新評価） ----
def search_monitoring(ctx: dict) -> dict:
    client = ctx["client"]
    toks = _toks(ctx)
    or_alert = _or_ilike(("title", "reason"), toks)
    or_theme = _or_ilike(("display_name", "monitoring_criteria"), toks)
    items = []
    if or_theme:
        for t in client.select("geo_monitoring_themes", {
                "select": "monitoring_theme_id,display_name,latest_assessment,last_checked_at,status,frequency",
                "or": or_theme, "limit": "5"}):
            la = t.get("latest_assessment") or {}
            text = " / ".join(f"{k}: {la[k]}" for k in ("current_state", "latest_change", "freshness") if la.get(k))
            items.append({
                "source_type": "monitoring_theme", "id": t["monitoring_theme_id"], "origin": "internal",
                "title": f"モニタリングテーマ: {t['display_name']}", "date": (t.get("last_checked_at") or "")[:10] or None,
                "url": None, "snippet": _trim(text) or "（最新評価なし）",
                "meta": {"status": t.get("status"), "frequency": t.get("frequency")}})
    if or_alert:
        params = {"select": "alert_id,title,reason,severity,alert_type,detected_at", "or": or_alert,
                  "order": "detected_at.desc", "limit": str(DEFAULT_LIMIT)}
        if ctx.get("date_from"):
            params["detected_at"] = f"gte.{ctx['date_from']}"
        for a in client.select("geo_monitoring_alerts", params):
            items.append({
                "source_type": "monitoring_alert", "id": a["alert_id"], "origin": "internal",
                "title": a.get("title"), "date": (a.get("detected_at") or "")[:10] or None, "url": None,
                "snippet": _trim(a.get("reason")), "meta": {"severity": a.get("severity"), "alert_type": a.get("alert_type")}})
    return _result("searched", None, items)


# ---- GDELTイベント（兆候用。件数・トーンの機械集計であり、確認済み事実ではない） ----
def search_events(ctx: dict) -> dict:
    or_expr = _or_ilike(("actor1_name", "actor2_name", "action_geo_full_name"), _toks(ctx))
    if not or_expr:
        return _result("searched", None, [])
    params = {"select": "external_event_id,actor1_name,actor2_name,action_geo_full_name,event_date,goldstein_scale,"
                        "avg_tone,num_mentions,source_url",
              "is_demo": "eq.false", "or": or_expr, "order": "event_date.desc", "limit": str(DEFAULT_LIMIT)}
    if ctx.get("date_from"):
        params["event_date"] = f"gte.{ctx['date_from']}"
    rows = ctx["client"].select("geo_external_events", params)
    items = [{
        "source_type": "external_event", "id": r["external_event_id"], "origin": "internal",
        "title": f"{r.get('actor1_name') or '?'} → {r.get('actor2_name') or '?'}（{r.get('action_geo_full_name') or '場所不明'}）",
        "date": (r.get("event_date") or "")[:10] or None, "url": r.get("source_url"),
        "snippet": f"GDELT機械抽出イベント。Goldstein={r.get('goldstein_scale')} 平均トーン={r.get('avg_tone')} 言及数={r.get('num_mentions')}",
        "meta": {"evidence_status": "unverified"}} for r in rows]
    return _result("searched", None, items)


def _contact_snippet(c: dict, inf: dict | None) -> str | None:
    """人が登録した情報と、会話ログからのAI推定を、混ぜずに別々に書く。"""
    parts = [f"【登録情報】所属・役職: {c.get('affiliation_title') or '未登録'} ／ 専門領域コメント: {c.get('expertise_note') or '未登録'}"]
    if inf and inf.get("summarized_log_count", inf.get("log_count")) == 0:
        # 面談ログはあるが、AI整理が1件も無い＝集計できていない。「AI推定」として何かを言わない
        parts.append(f"【会話ログからのAI推定】面談{inf['log_count']}回の記録はあるが、AI整理済みのログが無く、推定できません")
    elif inf:
        def names(k):
            return "、".join(f"{x['name']}({x['count']}回)" for x in inf[k][:5]) or "なし"
        parts.append(f"【会話ログからのAI推定・未確認】面談{inf['log_count']}回。扱われた話題: {names('topics')}。地域: {names('regions')}。"
                     f"本人の発言として特定された見解: {inf['speaker_view_count']}件")
        if inf["sample_views"]:
            parts.append("見解の例: " + " / ".join(inf["sample_views"]))
    return _trim(" ／ ".join(parts), 700)


# ---- 外部ネットワーク（外部コンタクト・面談ログ）。ユーザーが明示的に含めた場合のみ ----
def search_external_network(ctx: dict) -> dict:
    client, identity = ctx["client"], ctx.get("identity")
    try:
        if identity is None:
            raise ext_net.AccessError("認証が必要です")
        ext_net.require_member(identity)
    except ext_net.AccessError:
        return _result("no_access", "ログインしていない、または権限がないため検索していません")
    items = []
    for hit in ext_net.search_experts(client, identity, _toks(ctx), limit=8):
        c, inf = hit["contact"], hit["inferred"]
        items.append({
            "source_type": "contact", "id": c["contact_id"], "origin": "internal",
            "title": f"{c['name']}（{c['affiliation_title'] or '所属不明'}）", "date": c.get("last_contact_at"),
            "url": None, "snippet": _contact_snippet(c, inf),
            "meta": {"registered": True, "has_expertise_note": bool(c.get("expertise_note")), "inferred": bool(inf),
                     "registered_hits": hit["registered_hits"], "inferred_hits": hit["inferred_hits"], "log_count": c.get("log_count")}})
    seen_contacts: set = set()
    seen_logs = set()
    for term in _toks(ctx)[:4]:
        for lg in ext_net.list_logs(client, identity, q=term, date_from=ctx.get("date_from"), limit=8):
            if lg["log_id"] in seen_logs:
                continue
            seen_logs.add(lg["log_id"])
            names = "、".join(c["name"] or "" for c in lg["contacts"])
            items.append({
                "source_type": "contact_log", "id": lg["log_id"], "origin": "internal",
                "title": f"面談ログ {lg['contact_date']}（{names}）", "date": lg["contact_date"], "url": None,
                "snippet": _trim(lg.get("summary_snippet") or lg.get("memo_head"), 300),
                "meta": {"ai_summary": bool(lg.get("summary_snippet")), "contacts": names}})
    return _result("searched", "あなたが閲覧できる面談ログ・コンタクトのみが対象です", items)


# ---- Source Library（情報ソースの登録DB）。「どの情報源を確認すべきか」を探す ----
def search_source_library(ctx: dict) -> dict:
    """Source Libraryの既存の自然言語検索（意味検索。AI未設定ならキーワード検索）をそのまま使う。
    これは『世の中の出来事の根拠』ではなく『確認すべき情報源の候補』。回答では、確認ポイント・Watchpointsに使う。"""
    import geo_source_search_service as source_search
    res = source_search.search_sources(ctx["client"], ctx.get("azure_client"), ctx.get("model"), ctx.get("topic") or " ".join(ctx["terms"]))
    items = []
    for r in (res.get("sources") or [])[:6]:
        desc = (r.get("description") or "").replace("【AI生成・要確認】", "").strip()
        why = (r.get("match_reason") or "").strip()
        items.append({
            "source_type": "source", "id": r["source_id"], "origin": "internal", "title": r.get("name"), "date": None, "url": r.get("url"),
            "snippet": _trim(f"{why} ／ {desc}" if why else desc, 400),
            "meta": {"media_type": r.get("media_type"), "is_primary": bool(r.get("is_primary")), "reliability": r.get("reliability"),
                     "caution": r.get("match_caution") or None, "information_source": True}})
    reason = ("意味検索（情報ソースの特徴カードで選定）" if (res.get("interpretation") or {}).get("mode") == "semantic"
              else "キーワード検索")
    return _result("searched", f"{reason}。確認すべき情報源の候補であり、出来事の根拠ではありません", items)


# ---- Web検索（Perplexity）。テーマの文言だけを送る ----
def search_web(ctx: dict) -> dict:
    settings = pplx.get_settings()
    if not settings:
        return _result("unavailable", "Web検索のAPIキーが設定されていません")
    question = ctx["web_question"]
    try:
        res = pplx.search(settings, question=question, period_days=ctx.get("period_days"))
    except pplx.PerplexityError as exc:
        return _result("error", f"Web検索に失敗しました（{str(exc)[:80]}）")
    items = [{
        "source_type": "web", "id": c["url"], "origin": "web", "title": c.get("title") or c["url"],
        "date": (c.get("published_at") or "")[:10] or None, "url": c["url"],
        "snippet": None, "meta": {"date_known": bool(c.get("published_at"))}} for c in res["citations"][:DEFAULT_LIMIT]]
    # 検索AIの要約文は、複数ページをまとめたもの。特定のページの記述として扱うと出典を取り違えるため、
    # ページとは別の根拠（web_summary）にする。個別の主張の出典には、ページ側（URLのある根拠）を使うこと。
    if res.get("summary") and items:
        items.insert(0, {
            "source_type": "web", "id": "web_summary", "origin": "web", "title": "Web検索AIによる要約（下記ページ群の要約）",
            "date": None, "url": None, "snippet": _trim(res["summary"], 900),
            "meta": {"web_summary": True, "based_on": [i["url"] for i in items]}})
    return _result("searched", "公開Webのみ。社内資料の内容は送信していません", items)


ADAPTERS = {
    "reports": search_reports, "knowledge": search_knowledge, "articles": search_articles,
    "monitoring": search_monitoring, "events": search_events, "source_library": search_source_library,
    "external_network": search_external_network, "web": search_web,
}
SOURCE_LABELS = {
    "reports": "レポート・承認済みナレッジ", "knowledge": "共通知識基盤（専門家AIのナレッジ）", "articles": "記事DB",
    "monitoring": "モニタリング", "events": "GDELTイベント", "source_library": "情報ソースDB（Source Library）",
    "external_network": "外部ネットワーク（面談ログ）", "web": "Web検索",
}


def run_adapter(key: str, ctx: dict) -> dict:
    """例外はここで閉じ込め、失敗したソースを結果へ明記する（本文・メモはエラーに含めない）。"""
    try:
        res = ADAPTERS[key](ctx)
    except Exception as exc:  # noqa: BLE001
        text = str(exc)
        missing = any(m in text for m in ("PGRST205", "does not exist", "Could not find the table", "42P01"))
        resp = getattr(exc, "response", None)
        if resp is not None:
            try:
                missing = missing or any(m in (resp.text or "") for m in ("PGRST205", "42P01", "does not exist"))
            except Exception:  # noqa: BLE001
                pass
        return {"source": key, "label": SOURCE_LABELS[key], **(
            _result("unavailable", "テーブルが未作成のため検索できませんでした") if missing
            else _result("error", f"検索に失敗しました（{type(exc).__name__}）"))}
    return {"source": key, "label": SOURCE_LABELS[key], **res}


def refetch_by_refs(client, requester_context, identity, refs: list) -> tuple:
    """追加対話用: 保存された参照ID（source_type, id）から、**今のユーザーの権限で**資料を取り直す。
    権限がなくなった・存在しない参照は落とす。戻り値: (items, dropped_count)。
    ウェブの根拠はIDで再取得できないため、ここでは扱わない（呼び出し側が保持した引用を使う）。"""
    items, dropped = [], 0
    by_type: dict = {}
    for r in refs:
        if r.get("source_type") and r.get("id"):
            by_type.setdefault(r["source_type"], []).append(str(r["id"]))

    def _in(ids):
        return "in.(" + ",".join(ids) + ")"

    for stype, ids in by_type.items():
        if stype in ("report", "knowledge_item"):
            import geo_intelligence_ai_analysis_service as analysis
            sel = [{"source_type": stype, "id": i} for i in ids]
            entries, unauthorized = analysis._resolve_selected_sources(    # 既存の再認可処理をそのまま使う
                client, sel, requester_context=requester_context, is_internal_caller=False,
                caller_domain=None, include_demo=False)
            dropped += unauthorized
            for e in entries:
                eid = e.get("report_id") or e.get("knowledge_item_id")
                items.append({"source_type": stype, "id": eid, "origin": "internal", "title": e.get("title"),
                              "date": (e.get("date") or "")[:10] or None, "url": None, "snippet": _trim(e.get("summary")),
                              "meta": {"review_status": e.get("review_status")}})
        elif stype == "article":
            rows = client.select("geo_external_signals", {
                "select": "external_signal_id,title,summary,url,source_name,published_at", "external_signal_id": _in(ids),
                "is_demo": "eq.false"})
            items += [{"source_type": "article", "id": r["external_signal_id"], "origin": "internal", "title": r.get("title"),
                       "date": (r.get("published_at") or "")[:10] or None, "url": r.get("url"),
                       "snippet": _trim(r.get("summary")), "meta": {"source_name": r.get("source_name")}} for r in rows]
            dropped += len(ids) - len(rows)
        elif stype == "monitoring_alert":
            rows = client.select("geo_monitoring_alerts", {
                "select": "alert_id,title,reason,detected_at", "alert_id": _in(ids)})
            items += [{"source_type": stype, "id": r["alert_id"], "origin": "internal", "title": r.get("title"),
                       "date": (r.get("detected_at") or "")[:10] or None, "url": None, "snippet": _trim(r.get("reason")),
                       "meta": {}} for r in rows]
            dropped += len(ids) - len(rows)
        elif stype == "monitoring_theme":
            rows = client.select("geo_monitoring_themes", {
                "select": "monitoring_theme_id,display_name,latest_assessment,last_checked_at",
                "monitoring_theme_id": _in(ids)})
            items += [{"source_type": stype, "id": r["monitoring_theme_id"], "origin": "internal",
                       "title": f"モニタリングテーマ: {r['display_name']}", "date": (r.get("last_checked_at") or "")[:10] or None,
                       "url": None, "snippet": _trim(str(r.get("latest_assessment") or "")), "meta": {}} for r in rows]
            dropped += len(ids) - len(rows)
        elif stype == "external_event":
            rows = client.select("geo_external_events", {
                "select": "external_event_id,actor1_name,actor2_name,action_geo_full_name,event_date,source_url",
                "external_event_id": _in(ids)})
            items += [{"source_type": stype, "id": r["external_event_id"], "origin": "internal",
                       "title": f"{r.get('actor1_name') or '?'} → {r.get('actor2_name') or '?'}",
                       "date": (r.get("event_date") or "")[:10] or None, "url": r.get("source_url"),
                       "snippet": "GDELT機械抽出イベント（未検証）", "meta": {}} for r in rows]
            dropped += len(ids) - len(rows)
        elif stype in ("contact", "contact_log"):
            for i in ids:        # 外部ネットワークは1件ずつ、本人の可視性ルールで取り直す（見えなければ落とす）
                try:
                    if stype == "contact":
                        c = ext_net.get_contact(client, identity, i)
                        items.append({"source_type": stype, "id": i, "origin": "internal",
                                      "title": f"{c['name']}（{c['affiliation_title'] or '所属不明'}）", "date": c.get("last_contact_at"),
                                      "url": None, "snippet": _contact_snippet(c, c.get("inferred_expertise")),
                                      "meta": {"registered": True, "inferred": bool(c.get("inferred_expertise"))}})
                    else:
                        lg = ext_net.get_log(client, identity, i)
                        content = ((lg.get("ai_summary") or {}).get("content") or {})
                        items.append({"source_type": stype, "id": i, "origin": "internal",
                                      "title": f"面談ログ {lg['contact_date']}", "date": lg["contact_date"], "url": None,
                                      "snippet": _trim(content.get("summary") or lg.get("memo"), 300), "meta": {}})
                except (ext_net.ExternalNetworkError, AttributeError):
                    dropped += 1
        else:
            dropped += len(ids)
    return items, dropped
