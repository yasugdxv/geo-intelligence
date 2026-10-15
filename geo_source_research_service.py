"""Source指定Research: 「このSource群を使って、この問いを調べる」。

新しいResearch Engineは作らない。既存のgeo_expert_research_service.run_external_research
（Web検索 + url_citationのみ採用）を再利用し、Source Library由来の
  - 内部記事（geo_external_signals、選択Source/domain由来）
  - 外部Web検索（選択domainに限定）
を根拠として回答を作る。

★ Sourceへの限定の保証レベル（正直な記述）:
  Responses APIのweb_search_previewにはdomain allow-listが無いため、検索クエリへ
  `site:` 指定を入れて誘導し、返ってきたcitationを「選択domain（およびそのsubdomain）」で
  必ず後段フィルタする。選択domain外のURLは根拠に採用しない（=限定は後段フィルタで保証）。

★ Query Expansion（日本語Query→英語記事）: 検索の前段で、Queryを英訳・重要概念・同義語へ展開する
  （geo_source_query_expansion。問いの拡大はGuardで防ぐ）。検索語は original / translated / expanded を
  区別して保持し、結果に返す。最終回答の問いはユーザーの原文のまま。

★ Reference必須: 回答は必ずReference（Article title / Source / Published date / Original URL）
  つきで返す。根拠が1件も無い場合はLLM回答を生成せず「根拠なし」を返す（幻覚防止）。
  回答中の[n]は、返したreferencesのindexにのみ対応する（LLMにURLを生成させない）。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import geo_expert_research_service as expert_research
import geo_llm_common
import geo_source_library_service as lib
import geo_source_query_expansion as query_expansion

_ANSWER_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["answer", "used_references"],
    "properties": {"answer": {"type": "string"},
                   "used_references": {"type": "array", "items": {"type": "integer"}}},
}
_ANSWER_PROMPT = (
    "あなたは地政学インテリジェンスのリサーチアナリストです。与えられた『問い』に、提示された"
    "Reference（[番号]つき）の内容だけを根拠として日本語で回答してください。Referenceに無い事実は"
    "書かない。根拠が不十分な点は『根拠不足』と明記する。主張ごとに[番号]を付ける。"
    "used_referencesには実際に使った番号を入れる。")


def _in_list(values) -> str:
    return "in.(" + ",".join(str(v) for v in values) + ")"


def _domain_allowed(url_domain: str | None, allowed: set) -> bool:
    if not url_domain:
        return False
    return any(url_domain == d or url_domain.endswith("." + d) for d in allowed)


def resolve_sources_and_domains(client, source_ids: list) -> tuple:
    sources = client.select("geo_source_registry", {
        "select": "source_id,name,url,archived_at", "source_id": _in_list(source_ids)}) if source_ids else []
    domains: set = set()
    for s in sources:
        d = lib.normalize_domain(s.get("url"))
        if d:
            domains.add(d)
    for r in lib._safe_select(client, "geo_source_domains", {
            "select": "domain", "source_id": _in_list(source_ids), "match_enabled": "eq.true", "limit": "1000"}) \
            if source_ids else []:
        domains.add(r["domain"])
    return sources, domains


# 「質問に直接関連する」と扱う最小一致数（異なる検索語がタイトル/要約に何個含まれるか）。
# 国名・地名のような一般的な固有名詞2語だけの一致（例: "China"+"Taiwan"）で関連扱いにしないよう、
# 英語側（英訳・展開語）の語が多いほど必要な一致数を増やす（最大3）。原文側は最大2。
def _required_by_kind(search_terms: list) -> dict:
    n: dict = {}
    for t in search_terms:
        n.setdefault(t["kind"], set()).add(t["term"].lower())
    en = len(n.get("translated", set()) | n.get("expanded", set()))
    orig = len(n.get("original", set()))
    return {"english": None if en == 0 else (1 if en <= 1 else 2 if en <= 3 else 3),
            "original": None if orig == 0 else (1 if orig <= 1 else 2)}


def _is_direct_match(hits: list, required: dict) -> bool:
    en_hits = {h["term"].lower() for h in hits if h["kind"] in ("translated", "expanded")}
    orig_hits = {h["term"].lower() for h in hits if h["kind"] == "original"}
    return bool((required["english"] and len(en_hits) >= required["english"]) or
                (required["original"] and len(orig_hits) >= required["original"]))


def _score_article(article: dict, search_terms: list) -> list:
    """記事に含まれる検索語（出自つき）を返す。"""
    text = f"{article.get('title') or ''} {article.get('summary') or ''}".lower()
    return [{"term": t["term"], "kind": t["kind"]} for t in search_terms if t["term"].lower() in text]


def _internal_articles(client, source_ids: list, domains: set, search_terms: list, limit: int = 12,
                       supplement_min: int = 3) -> list:
    """選択Source/domainの記事を検索語（original/translated/expanded）で探し、一致数で順位付けする。
    戻り値の各記事は match_type: "matched"（質問に直接関連）/ "supplementary"（直近記事での補完）と
    matched_terms（どの出自の語に一致したか）を持つ。補完は、matchedが少ない場合のみ直近記事で足す。"""
    cols = "external_signal_id,title,summary,url,source_name,source_domain,published_at,created_at"
    since = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()   # indexのあるpublished_atで下限
    found: dict = {}
    queries = []
    if source_ids:
        queries.append({"source_id": _in_list(source_ids)})
    if domains:
        queries.append({"source_domain": _in_list(sorted(domains))})
    terms = [t["term"].replace(",", " ").replace("(", " ").replace(")", " ") for t in search_terms][:12]
    for base in queries:
        try:
            if terms:
                cond = ",".join(f"title.ilike.*{t}*,summary.ilike.*{t}*" for t in terms)
                for r in client.select("geo_external_signals", {
                        "select": cols, **base, "published_at": f"gte.{since}", "or": f"({cond})",
                        "order": "published_at.desc", "limit": "80"}):
                    found.setdefault(r["external_signal_id"], r)
        except Exception as exc:  # noqa: BLE001
            if not lib._is_missing_column_error(exc):
                raise
    required = _required_by_kind(search_terms)
    matched = []
    for r in found.values():
        hits = _score_article(r, search_terms)
        if _is_direct_match(hits, required):
            r["match_type"], r["matched_terms"] = "matched", hits
            matched.append(r)
    matched.sort(key=lambda r: (len({h["term"].lower() for h in r["matched_terms"]}), r.get("published_at") or ""),
                 reverse=True)
    out = matched[:limit]
    # 一致が少なすぎる場合のみ、選択Sourceの直近記事で補完する（関連性は保証しない=supplementary）
    if len(out) < supplement_min:
        have = {r["external_signal_id"] for r in out}
        for base in queries:
            try:
                for r in client.select("geo_external_signals", {
                        "select": cols, **base, "published_at": f"gte.{since}",
                        "order": "published_at.desc", "limit": str(limit)}):
                    if r["external_signal_id"] not in have and len(out) < limit:
                        have.add(r["external_signal_id"])
                        r["match_type"], r["matched_terms"] = "supplementary", []
                        out.append(r)
            except Exception as exc:  # noqa: BLE001
                if not lib._is_missing_column_error(exc):
                    raise
    return out


def run_source_research(client, azure_client, model: str, *, theme: str, query: str,
                        source_ids: list, use_web: bool = True) -> dict:
    """戻り値: {answer, references[], sources[], notes[], errors[], query_expansion}
    references: {index, article_title, source, published_at, original_url, origin(internal|web),
                 match_type(matched|supplementary), matched_terms[{term, kind}]}"""
    query = (query or "").strip()
    if not query:
        raise lib.SourceValidationError("調査したい問い(query)を入力してください")
    if not source_ids:
        raise lib.SourceValidationError("調査に使うSourceを1つ以上選択してください")

    sources, domains = resolve_sources_and_domains(client, source_ids)
    notes, errors, references = [], [], []

    # Query Expansion（日本語Query→英語検索語）。問い自体（query）は変更しない
    expansion = query_expansion.expand_query(azure_client, model, query)
    if expansion.get("error"):
        notes.append(expansion["error"])
    dropped = expansion.get("guard", {}).get("dropped") or []
    if dropped:
        notes.append(f"問いを広げる恐れのある検索語{len(dropped)}件はGuardにより破棄しました。")

    for a in _internal_articles(client, source_ids, domains, expansion["search_terms"]):
        if not a.get("url"):
            continue
        references.append({
            "article_title": a.get("title"), "source": a.get("source_name") or a.get("source_domain"),
            "published_at": a.get("published_at") or a.get("created_at"), "original_url": a["url"],
            "origin": "internal", "snippet": a.get("summary"), "external_signal_id": a["external_signal_id"],
            "match_type": a["match_type"], "matched_terms": a["matched_terms"],
            "keyword_matched": a["match_type"] == "matched"})
    n_matched = sum(1 for r in references if r["match_type"] == "matched")
    n_supp = len(references) - n_matched
    if n_supp:
        notes.append(
            f"問いに直接関連する記事が{n_matched}件しか見つからなかったため、選択Sourceの直近記事{n_supp}件を"
            "「補完」として表示しています（質問に直接関連するEvidenceではありません。関連性は保証されません）。")
    if not references:
        notes.append("選択Sourceの取得済み記事が見つかりませんでした（データ取得が止まっている可能性があります）。")

    if use_web and azure_client is not None and domains:
        site_clause = " OR ".join(f"site:{d}" for d in sorted(domains)[:8])
        # Web検索には英訳Queryを使う（原文の問いはcontextに保持）
        web_query = expansion.get("translated") or query
        try:
            res = expert_research.run_external_research(
                azure_client, model, question=f"{web_query} ({site_clause})",
                context=(f"テーマ: {theme or '(指定なし)'}。ユーザーの問い（原文）: {query}。"
                         f"検索対象は次のdomainに限定: {', '.join(sorted(domains)[:8])}"),
                search_lens="選択された情報ソースのdomain内の記事")
            dropped_n = 0
            seen = {r["original_url"] for r in references}
            for c in res.get("citations", []):
                d = lib.normalize_domain(c.get("url"))
                if not _domain_allowed(d, domains):
                    dropped_n += 1
                    continue
                if c["url"] in seen:
                    continue
                seen.add(c["url"])
                references.append({
                    "article_title": c.get("title"), "source": d, "published_at": None,
                    "original_url": c["url"], "origin": "web", "snippet": res.get("summary"),
                    "match_type": "matched", "matched_terms": [], "keyword_matched": True})
            if dropped_n:
                notes.append(f"選択Source外のWeb検索結果{dropped_n}件は根拠から除外しました。")
        except Exception as exc:  # noqa: BLE001 — Web検索の失敗で内部記事の結果まで落とさない
            errors.append(f"Web検索に失敗しました: {exc}")
    elif use_web and azure_client is None:
        errors.append("AI分析エンジンが設定されていないためWeb検索は行っていません。")

    # matchedを先に、supplementaryを後に並べて番号を振る（補完が質問に直接関連するEvidenceに見えないように）
    references.sort(key=lambda r: 0 if r["match_type"] == "matched" else 1)
    for i, r in enumerate(references, 1):
        r["index"] = i

    answer = None
    matched_refs = [r for r in references if r["match_type"] == "matched"]
    if not references:
        notes.append("根拠となる記事が無いため、回答は生成していません。")
    elif not matched_refs:
        notes.append("質問に直接関連する記事が見つからなかったため、回答は生成していません（補完記事は参考表示のみ）。")
    elif azure_client is None:
        errors.append("AI分析エンジンが設定されていないため回答は生成していません（Referenceのみ表示）。")
    else:
        # 回答の根拠にするのは「直接関連する」記事だけ。補完記事はLLMへ渡さない
        ref_text = "\n".join(
            f"[{r['index']}] {r['article_title'] or '(無題)'} / {r['source'] or '不明'} / "
            f"{r['published_at'] or '日付不明'}\n    "
            f"{'[Web検索ツールによる要約。記事本文ではない] ' if r['origin'] == 'web' else ''}"
            f"{(r.get('snippet') or '')[:400]}" for r in matched_refs)
        try:
            result = geo_llm_common.call_llm_structured(
                azure_client, model, _ANSWER_PROMPT,
                f"# テーマ\n{theme}\n\n# 問い\n{query}\n\n# Reference\n{ref_text}",
                _ANSWER_SCHEMA, "source_research_answer", max_attempts=2)
            data = result["data"]
            valid = {r["index"] for r in matched_refs}
            answer = {"text": data["answer"],
                      "used_references": [n for n in data.get("used_references", []) if n in valid]}
        except geo_llm_common.GeoLLMError as exc:
            errors.append(f"回答の生成に失敗しました（Referenceは表示しています）: {exc}")

    return {"theme": theme, "query": query, "answer": answer, "references": references,
            "query_expansion": {k: expansion.get(k) for k in (
                "version", "original", "translated", "key_concepts", "expanded_terms", "search_terms", "used_llm", "guard")},
            "sources": [{"source_id": s["source_id"], "name": s["name"], "url": s.get("url"),
                         "archived": bool(s.get("archived_at"))} for s in sources],
            "searched_domains": sorted(domains), "notes": notes, "errors": errors}
