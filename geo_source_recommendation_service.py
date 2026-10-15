"""Theme / Article群 → 「どのSourceがこのテーマに有効か」の逆引きRecommendation（P1、決定的ルール）。

LLMは使わない。既存データから算出できる指標だけを使い、必ず「なぜおすすめか」の短い理由を
返す（Freeze済みTheme Candidate生成ロジックには触れず、読み取りのみ）。

指標（存在するデータの範囲で算出。無い指標はスコア0で、理由にも出さない）:
- theme_article: Theme関連Article（候補のsource_external_signal_ids等）をそのSourceが何件出したか
- tag_match / text_match: Source Tag・説明文・得意領域とThemeのactors/regions/themesの一致
- primary: 一次情報Source（is_primary / source_tier=一次）
- recency: 直近90日の取得記事数
- usage: 同じThemeに既にlink済み（manual/usage_derived）
データ停止（GDELT）中は article系指標が弱くなり、Tag/テキスト一致中心の推薦になる。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import geo_source_library_service as lib

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]{2,}|[぀-ヿ一-鿿]{2,}")
_STOP = {"the", "and", "for", "with", "から", "について", "および", "する", "こと"}


def _terms(theme: dict) -> list:
    terms: list = []
    for key in ("themes", "actors", "regions", "keywords", "tags"):
        for v in theme.get(key) or []:
            if isinstance(v, str) and v.strip():
                terms.append(v.strip())
    for w in _WORD_RE.findall(theme.get("title") or ""):
        if w.lower() not in _STOP:
            terms.append(w)
    seen, out = set(), []
    for t in terms:
        k = t.lower()
        if k not in seen:
            seen.add(k)
            out.append(t)
    return out[:30]


def _in_list(values) -> str:
    return "in.(" + ",".join(str(v) for v in values) + ")"


def recommend_sources(client, theme: dict, *, limit: int = 12) -> dict:
    """theme: {title, actors[], regions[], themes[], keywords[], theme_id?, external_signal_ids[]?}"""
    terms = _terms(theme)
    lterms = [t.lower() for t in terms]

    sources = client.select("geo_source_registry", {
        "select": "*", "archived_at": "is.null", "limit": "500"})
    if not sources:
        return {"recommendations": [], "unregistered_domains": [], "basis": {"terms": terms}}
    ids = [s["source_id"] for s in sources]

    tags: dict = {}
    for t in lib._safe_select(client, "geo_source_tags", {
            "select": "source_id,tag_value,is_confirmed", "source_id": _in_list(ids), "limit": "10000"}):
        tags.setdefault(t["source_id"], []).append(t)

    # Theme関連Article → Source（source_idがある記事）/ 未登録domain
    theme_articles: dict = {}
    unregistered: dict = {}
    signal_ids = [s for s in (theme.get("external_signal_ids") or []) if s][:200]
    if signal_ids:
        rows = lib._safe_select(client, "geo_external_signals", {
            "select": "external_signal_id,source_id,source_domain", "external_signal_id": _in_list(signal_ids),
            "limit": "500"})
        resolved: dict = {}   # domain → 一意に確定できる登録Source（取り込み後に追加された記事にはsource_idが無いため）
        for r in rows:
            if r.get("source_id"):
                theme_articles[r["source_id"]] = theme_articles.get(r["source_id"], 0) + 1
            elif r.get("source_domain"):
                d = lib.normalize_domain(r["source_domain"])
                if not d:
                    continue
                if d not in resolved:
                    resolved[d] = lib.resolve_source_for_domain(client, d)
                m = resolved[d]
                if m["status"] == "unique":
                    # 安全な照合(一意に確定できる場合のみ)でこのテーマの根拠記事を出したSourceとして数える。
                    # ambiguousは推測で寄せない（未登録扱いにもしない）
                    theme_articles[m["source_id"]] = theme_articles.get(m["source_id"], 0) + 1
                elif m["status"] == "unmatched":
                    unregistered[d] = unregistered.get(d, 0) + 1

    stats = lib._article_stats_by_source(client, ids)
    linked: set = set()
    if theme.get("theme_id"):
        linked = set(lib._source_ids_by_theme(client, theme["theme_id"]))

    cutoff_90 = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    scored = []
    for s in sources:
        sid = s["source_id"]
        reasons, score = [], 0.0
        n_art = theme_articles.get(sid, 0)
        if n_art:
            score += min(n_art, 10) * 3
            reasons.append(f"このテーマ関連の記事を{n_art}件取得")
        matched_tags = [t["tag_value"] for t in tags.get(sid, [])
                        if any(lt == t["tag_value"].lower() or lt in t["tag_value"].lower() for lt in lterms)]
        if matched_tags:
            score += sum(2.0 if next((x for x in tags[sid] if x["tag_value"] == m), {}).get("is_confirmed") else 1.0
                         for m in matched_tags)
            reasons.append("得意領域Tagが一致: " + ", ".join("#" + m for m in matched_tags[:4]))
        haystack = " ".join(str(s.get(k) or "") for k in ("name", "description", "thematic_scope",
                                                          "geographic_scope", "notes")).lower()
        text_hits = [t for t in terms if t.lower() in haystack and t.lower() not in [m.lower() for m in matched_tags]]
        if text_hits:
            score += min(len(text_hits), 4) * 1.0
            reasons.append("説明・得意領域に「" + "・".join(text_hits[:3]) + "」を含む")
        if s.get("is_primary") or s.get("source_tier") == "一次":
            if score > 0:  # 一次情報は「関連がある場合」の加点（関連無しで一次だけを推さない）
                score += 2
                reasons.append("一次情報Source")
        st = stats.get(sid, {})
        if st.get("last_article_at") and st["last_article_at"] >= cutoff_90 and score > 0:
            score += 1
            reasons.append(f"直近90日に記事{st.get('count_30d', 0)}件（30日）")
        if sid in linked:
            score += 2
            reasons.append("このテーマに紐付け済み")
        if score > 0:
            scored.append({"source": s, "score": round(score, 2), "reasons": reasons})

    scored.sort(key=lambda x: (-x["score"], x["source"]["name"]))
    recs = []
    for item in scored[:limit]:
        s = item["source"]
        recs.append({
            "source_id": s["source_id"], "name": s["name"], "url": s.get("url"),
            "media_type": s.get("media_type"), "is_primary": bool(s.get("is_primary")),
            "score": item["score"], "reason": "。".join(item["reasons"][:3]) + "。",
            "reasons": item["reasons"],
        })
    unreg = sorted(({"domain": d, "article_count": n} for d, n in unregistered.items()),
                   key=lambda x: -x["article_count"])[:10]
    return {"recommendations": recs, "unregistered_domains": unreg,
            "basis": {"terms": terms, "theme_article_count": sum(theme_articles.values()),
                      "note": "記事の紐付け(source_id)が無い期間は、Tag/説明文の一致が中心の推薦になります。"}}
