"""ソース特徴カードの下書きを作る（自然言語のソース検索用）。

既定（draft）: DBは読むだけ。各ソースの名称・URL・種別・備考・既存項目・集めた記事の見出しから、AIが
「何を扱うか／どの地域か／どんな視点か／どんな調査に向くか」の案を作り、確認用のファイルに出す。
  出力: evaluation/SOURCE_PROFILE_DRAFT.json（機械用）・SOURCE_PROFILE_DRAFT.md（人が読む用）
--apply <json>: 確認・修正済みのJSONを、DBの空欄の項目にだけ書き込む（既存の入力は上書きしない）。
  書き込む項目: description / thematic_scope / geographic_scope / media_type / languages
  description の先頭に「【AI生成・要確認】」を付ける。1件ずつ監査ログ(geo_audit)に残す。

使い方:
  python scripts/geo_source_profile_draft.py                    # 下書きを作る（DB書き込みなし）
  python scripts/geo_source_profile_draft.py --limit 10         # 動作確認用に10件だけ
  python scripts/geo_source_profile_draft.py --apply evaluation/SOURCE_PROFILE_DRAFT.json   # 反映（要・事前確認）
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from urllib import robotparser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_client import make_openai_client            # noqa: E402
from config_utils import load_config, make_proxies    # noqa: E402
from geo_db_client import SupabaseClient              # noqa: E402
import geo_audit                                      # noqa: E402
import geo_documents_store                            # noqa: E402
import geo_llm_common                                 # noqa: E402
import geo_perplexity_search as pplx                  # noqa: E402
import geo_source_library_service as lib              # noqa: E402

AI_MARK = "【AI生成・要確認】"
OUT_JSON = ROOT / "evaluation" / "SOURCE_PROFILE_DRAFT.json"
OUT_MD = ROOT / "evaluation" / "SOURCE_PROFILE_DRAFT.md"
BATCH = 6
ARTICLES_PER_SOURCE = 10

_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["cards"],
    "properties": {"cards": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["source_id", "description", "topics", "regions", "perspective", "media_type",
                     "languages", "good_for", "confidence"],
        "properties": {
            "source_id": {"type": "string"},
            "description": {"type": "string"},
            "topics": {"type": "array", "items": {"type": "string"}},
            "regions": {"type": "array", "items": {"type": "string"}},
            "perspective": {"type": "string"},
            "media_type": {"type": "string", "enum": ["", *lib.MEDIA_TYPES]},
            "languages": {"type": "array", "items": {"type": "string"}},
            "good_for": {"type": "string"},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]}}}}},
}
_PROMPT = (
    "あなたは地政学情報ソースDBの編集者です。各ソースについて、検索で見つけやすくするための『特徴カード』を"
    "日本語で作ってください。\n"
    "- description: そのソースが何者で、何を発信するか（1〜2文、100字程度）。\n"
    "- topics: 主に扱う話題（最大6。例: 輸出管理、制裁、選挙、海運・海上安全、エネルギー、台湾海峡）。\n"
    "- regions: 主に扱う国・地域（最大4）。\n"
    "- perspective: 発信の立場（例: 当事者の公式発表／通信社の報道／独立系シンクタンクの分析／業界団体／商業データ）。\n"
    "- media_type: 明確な場合のみ。不明なら空文字。languages: 主な言語コード(ja/en/zh等)。\n"
    "- good_for: どんな調査に向くか（1文。例:『米国の輸出規制の一次情報を確認する』）。\n"
    "- confidence: 提供された情報（名称・URL・種別・備考・記事や文書のタイトル・サイト自身の説明）から言える度合い。サイト自身の説明（ページのタイトル・説明文・ナビゲーションのカテゴリ・主な見出し）と、外部のWeb調査（第三者の情報）があれば、それらを最優先の根拠にする。サイト自身の説明と外部の評価が食い違う場合（自称と実態が違う等）は、外部の調査も踏まえ、perspectiveに両方を簡潔に書く。外部調査の出典が無い・曖昧な内容は使わない。メール配信の登録案内・クッキー・購読・広告などの定型文は、そのサイトの扱う話題の根拠にしない（系列サイト共通の文面のことがある）。カテゴリ名と見出しが示す主題・論調（例: 国境・移民を重視する、特定の立場に偏る）が読み取れる場合は、perspectiveに書く。根拠どうしが食い違う、または主題が読み取れない場合は、無理に断定せず、confidenceをlowにする。推測が多いならlow。\n"
    "【ルール】提供された情報と、そのソースについて広く知られた事実だけを使う。確信が無い点は書かず、"
    "confidenceをlowにする。タイトルに無い話題を、それらしく足さない。")


def _articles_for(client, source_id: str, url: str | None = None) -> list:
    """そのソースの記事・文書のタイトル。記事(geo_external_signals)→取得済み文書(geo_documents)→URLのドメインで探す。
    取得済みの文書は、記事テーブルとは別に、本文つきで保存されている（多くのソースはこちらにある）。"""
    def q(table: str, params: dict) -> list:
        try:
            rows = client.select(table, {"select": "title", "limit": str(ARTICLES_PER_SOURCE), **params})
        except Exception:  # noqa: BLE001 — 列が無い等でも、見出し無しで続ける
            return []
        return [r["title"] for r in rows if r.get("title")]

    titles = q("geo_external_signals", {"source_id": f"eq.{source_id}", "order": "published_at.desc"})
    if len(titles) < ARTICLES_PER_SOURCE:
        titles += [t for t in q("geo_documents", {"source_id": f"eq.{source_id}", "order": "created_at.desc"})
                   if t not in titles][:ARTICLES_PER_SOURCE - len(titles)]
    if titles:
        return titles
    domain = (urlparse(url or "").netloc or "").lower().removeprefix("www.")
    if not domain:
        return []
    return q("geo_external_signals", {"source_domain": f"in.({domain},www.{domain})", "order": "published_at.desc"})


def load_sources(client, limit: int | None) -> list:
    rows = client.select("geo_source_registry", {
        "select": "source_id,name,url,source_type,media_type,region,description,thematic_scope,"
                  "geographic_scope,languages,notes,is_primary,trust_tier",
        "archived_at": "is.null", "order": "name.asc", "limit": str(limit or 500)})
    with ThreadPoolExecutor(max_workers=6) as ex:
        arts = list(ex.map(lambda r: _articles_for(client, r["source_id"], r.get("url")), rows))
    for r, a in zip(rows, arts):
        r["_articles"] = a
    return rows


def _describe(s: dict) -> str:
    known = {k: s.get(k) for k in ("source_type", "media_type", "region", "description", "thematic_scope",
                                    "geographic_scope", "languages", "notes", "trust_tier") if s.get(k)}
    arts = "\n".join(f"    - {t}" for t in s["_articles"]) or "    （記事なし）"
    site = (f"\n  サイト自身の説明（公開ページを1回読んだ抜粋）:\n    {s['_site_text']}" if s.get("_site_text") else "")
    if s.get("_web_check"):
        site += f"\n  外部のWeb調査（第三者の情報。出典URLつき）:\n    {s['_web_check']}"
    return (f"[{s['source_id']}] 名称: {s['name']}\n  URL: {s.get('url')}\n  既存の情報: "
            f"{json.dumps(known, ensure_ascii=False)}\n  集めた記事・文書のタイトル:\n{arts}{site}")


ABOUT_PATHS = ("/about", "/about-us", "/about/", "/en/about", "/who-we-are")
SITE_TEXT_CHARS = 1500
CRAWL_WORKERS = 4


def _allowed_by_robots(url: str, proxies: dict, verify: bool) -> bool:
    """robots.txt（取得できなければ許可扱い）で、全ボット向けに禁止されているページは読まない。"""
    import requests
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    try:
        r = requests.get(f"{parts.scheme}://{parts.netloc}/robots.txt", proxies=proxies, verify=verify, timeout=10)
    except requests.RequestException:
        return True
    if r.status_code != 200 or "<html" in r.text[:200].lower():
        return True
    rp = robotparser.RobotFileParser()
    rp.parse(r.text.splitlines())
    return rp.can_fetch("*", url)


_NOISE = ("newsletter", "subscribe", "cookie", "privacy policy", "terms of", "sign in", "log in", "login", "menu", "search",
          "skip to", "contact us", "advertis", "メールマガジン", "ログイン", "検索")


def _identity_from_html(html: str) -> dict:
    """HTMLから、サイトの性格を表す情報（タイトル・説明文・ナビのカテゴリ名・主な見出し）を取り出す。
    本文の抜粋より、定型文（メール登録の案内など）に左右されにくい。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")

    def meta(*names) -> str:
        for n in names:
            tag = soup.find("meta", attrs={"name": n}) or soup.find("meta", attrs={"property": n})
            if tag and tag.get("content"):
                return " ".join(tag["content"].split())[:300]
        return ""

    def clean(texts: list, limit: int, maxlen: int = 40) -> list:
        out, seen = [], set()
        for t in texts:
            t = " ".join((t or "").split())
            k = t.lower()
            if 2 <= len(t) <= maxlen and k not in seen and not any(w in k for w in _NOISE):
                seen.add(k)
                out.append(t)
            if len(out) >= limit:
                break
        return out

    nav_roots = soup.find_all(["nav", "header"]) or [soup]
    nav = clean([a.get_text(" ") for r in nav_roots for a in r.find_all("a")], 25)
    heads = clean([h.get_text(" ") for h in soup.find_all(["h1", "h2", "h3"])], 10, maxlen=120)
    return {"title": " ".join((soup.title.get_text() if soup.title else "").split())[:150],
            "description": meta("description", "og:description"), "site_name": meta("og:site_name"),
            "nav": nav, "headings": heads}


def _fetch_identity(url: str, proxies: dict, verify: bool) -> dict:
    import requests
    try:
        r = requests.get(url, proxies=proxies, verify=verify, timeout=20, headers=geo_documents_store._HEADERS)
        r.raise_for_status()
    except requests.RequestException as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:60]}"}
    ctype = r.headers.get("Content-Type", "")
    if "html" not in ctype.lower():
        return {"ok": False, "error": f"HTMLではない({ctype[:30]})"}
    return {"ok": True, **_identity_from_html(r.text)}


def crawl_site_text(url: str, proxies: dict, verify: bool) -> dict:
    """トップと「About」ページを、1回ずつだけ読む（DBには保存しない）。サイトの性格を表す情報
    （タイトル・説明文・ナビのカテゴリ・主な見出し）を先に、本文の抜粋を参考として最後に並べる。
    戻り値: {"status", "text", "pages"}"""
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    if not parts.netloc:
        return {"status": "URLなし", "text": "", "pages": []}
    base = f"{parts.scheme}://{parts.netloc}"
    blocks, bodies, pages, status = [], [], [], "取得できず"
    for path in ("",) + ABOUT_PATHS:
        target = base + path
        if not _allowed_by_robots(target, proxies, verify):
            status = "robots.txtで禁止"
            if not path:
                break
            continue
        ident = _fetch_identity(target, proxies, verify)
        if not ident["ok"]:
            if not path:
                status = f"取得失敗: {ident['error']}"
            continue
        label = "トップ" if not path else path
        lines = [f"[{label}] タイトル: {ident['title']}"]
        if ident["site_name"]:
            lines.append(f"  サイト名: {ident['site_name']}")
        if ident["description"]:
            lines.append(f"  説明文: {ident['description']}")
        if ident["nav"]:
            lines.append(f"  ナビのカテゴリ: {' | '.join(ident['nav'])}")
        if ident["headings"]:
            lines.append(f"  主な見出し: {' / '.join(ident['headings'])}")
        blocks.append("\n    ".join(lines))
        pages.append(target)
        status = "取得済み"
        if path:
            break      # Aboutが取れたら、それ以上は読まない
    # 参考の本文抜粋（定型文を含むことがある）。取り出せた場合だけ、最後に付ける
    if pages:
        r = geo_documents_store.fetch_url_for_backfill(pages[0], proxies=proxies, verify=verify)
        if r["ok"]:
            bodies.append("参考（本文の抜粋。メール登録などの定型文を含むことがある）: " + " ".join(r["text"].split())[:500])
    text = "\n    ".join(blocks + bodies)[:SITE_TEXT_CHARS * 2]
    return {"status": status, "text": text, "pages": pages}


WEB_CHECK_WORKERS = 3


def web_check(settings: dict, name: str, url: str | None) -> dict:
    """そのソース自体について、Web検索（Perplexity）で第三者の情報を調べる。戻り値: {"text", "urls"}。失敗は空。"""
    q = (f"「{name}」{f'（{url}）' if url else ''} とはどのような組織・メディアか。運営主体、主に扱う話題と地域、"
         "編集方針や論調（立場の偏りがあれば）、第三者からの信頼性の評価を、出典つきで簡潔に。")
    try:
        r = pplx.search(settings, question=q)
    except pplx.PerplexityError as exc:
        return {"text": "", "urls": [], "error": str(exc)[:80]}
    urls = [c["url"] for c in r["citations"]][:5]
    text = (r["summary"] or "")[:900]
    return {"text": f"{text}（出典: {', '.join(urls)}）" if text and urls else text, "urls": urls}


def crawl_and_regenerate(cfg: dict, azure_client, model: str) -> None:
    """既存の下書きのうち、根拠が薄いソース（タイトル1件以下、または確信度low）だけを、サイト自身の説明
    （公開ページを1回読む）と、外部のWeb調査（Perplexity）の両方を使ってカードを作り直し、下書きへ戻す。
    社内ソース・URLなしは対象外。DBへは書き込まない。"""
    data = json.loads(OUT_JSON.read_text(encoding="utf-8"))
    shutil.copy(OUT_JSON, OUT_JSON.with_name("SOURCE_PROFILE_DRAFT_before_crawl.json"))
    proxies, verify = make_proxies(cfg), cfg.get("ssl", {}).get("verify", True)
    settings = pplx.get_settings()
    targets = [o for o in data if o["url"] and not o["name"].startswith("Internal")
               and (o["article_count_sampled"] <= 1 or (o["card"] or {}).get("confidence") == "low")]
    print(f"対象 {len(targets)}件。サイトの公開ページを1回読み、Webで第三者の情報も調べます"
          f"（robots.txtを尊重。Perplexity: {'あり' if settings else '未設定のため省略'}）")
    with ThreadPoolExecutor(max_workers=CRAWL_WORKERS) as ex:
        crawled = list(ex.map(lambda o: crawl_site_text(o["url"], proxies, verify), targets))
    checks = [{"text": "", "urls": []}] * len(targets)
    if settings:
        with ThreadPoolExecutor(max_workers=WEB_CHECK_WORKERS) as ex:
            checks = list(ex.map(lambda o: web_check(settings, o["name"], o["url"]), targets))
    print(f"サイトを読めた: {sum(1 for c in crawled if c['status'] == '取得済み')}/{len(targets)}"
          f" | Web調査あり: {sum(1 for c in checks if c['text'])}/{len(targets)}")
    sources = []
    for o, c, w in zip(targets, crawled, checks):
        o["crawl"] = {"status": c["status"], "pages": c["pages"], "excerpt": c["text"][:600]}
        o["web_check"] = {"urls": w["urls"], "excerpt": w["text"][:500]}
        if c["text"] or w["text"]:
            sources.append({"source_id": o["source_id"], "name": o["name"], "url": o["url"], "_articles": [],
                            "_site_text": c["text"], "_web_check": w["text"],
                            **{k: v for k, v in (o["existing"] or {}).items()}, "source_type": None})
    cards = {c["source_id"]: c for c in make_cards(azure_client, model, sources)} if sources else {}
    for o in targets:
        if o["source_id"] in cards:
            o["card"] = cards[o["source_id"]]
            o["card"]["basis"] = "サイト自身の説明（1回読み取り）と外部のWeb調査"
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    write_md_from_data(data)
    print(f"完了: {len(cards)}件のカードを作り直しました → {OUT_MD}")


def write_md_from_data(data: list) -> None:
    lines = ["# ソース特徴カード（AI生成の下書き。確認・修正してから反映する）\n",
             "- 修正する場合は `SOURCE_PROFILE_DRAFT.json` の `card` を直接編集する。反映しないソースは `card` を null にする。",
             "- 既に入力済みの項目は、反映時に上書きしない。\n"]
    for o in data:
        c = o["card"]
        crawl = o.get("crawl")
        tag = f"  サイト読み取り: {crawl['status']}" if crawl else ""
        lines.append(f"## {o['name']}  \n`{o['source_id']}` {o['url'] or ''}  記事・文書のタイトル{o['article_count_sampled']}件を参照{tag}")
        if not c:
            lines.append("- （生成なし）\n")
            continue
        lines += [f"- 確信度: **{c['confidence']}**　媒体: {c['media_type'] or '不明'}　言語: {', '.join(c['languages']) or '不明'}",
                  f"- 説明: {c['description']}", f"- 話題: {' / '.join(c['topics'])}　地域: {' / '.join(c['regions'])}",
                  f"- 立場: {c['perspective']}", f"- 向く調査: {c['good_for']}\n"]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def make_cards(azure_client, model: str, sources: list) -> list:
    cards, ids = [], {s["source_id"] for s in sources}
    for i in range(0, len(sources), BATCH):
        chunk = sources[i:i + BATCH]
        user = "\n\n".join(_describe(s) for s in chunk)
        try:
            res = geo_llm_common.call_llm_structured(
                azure_client, model, _PROMPT, user, _SCHEMA, "source_profile_draft", max_attempts=3)
        except geo_llm_common.GeoLLMError as exc:
            print(f"  ! {i + 1}〜{i + len(chunk)}件目の生成に失敗: {str(exc)[:80]}")
            continue
        for c in res["data"]["cards"]:
            if c["source_id"] in ids:
                cards.append(c)
        print(f"  {min(i + BATCH, len(sources))}/{len(sources)} 件")
    return cards


def write_outputs(sources: list, cards: list) -> None:
    by_id = {c["source_id"]: c for c in cards}
    out = []
    for s in sources:
        c = by_id.get(s["source_id"])
        out.append({"source_id": s["source_id"], "name": s["name"], "url": s.get("url"),
                    "existing": {k: s.get(k) for k in ("description", "thematic_scope", "geographic_scope",
                                                       "media_type", "region", "languages") if s.get(k)},
                    "article_count_sampled": len(s["_articles"]), "card": c})
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# ソース特徴カード（AI生成の下書き。確認・修正してから反映する）\n",
             "- 修正する場合は `SOURCE_PROFILE_DRAFT.json` の `card` を直接編集する。反映しないソースは `card` を null にする。",
             "- 既に入力済みの項目は、反映時に上書きしない。\n"]
    for o in out:
        c = o["card"]
        lines.append(f"## {o['name']}  \n`{o['source_id']}` {o['url'] or ''}  記事・文書のタイトル{o['article_count_sampled']}件を参照")
        if not c:
            lines.append("- （生成なし）\n")
            continue
        lines += [f"- 確信度: **{c['confidence']}**　媒体: {c['media_type'] or '不明'}　言語: {', '.join(c['languages']) or '不明'}",
                  f"- 説明: {c['description']}", f"- 話題: {' / '.join(c['topics'])}　地域: {' / '.join(c['regions'])}",
                  f"- 立場: {c['perspective']}", f"- 向く調査: {c['good_for']}\n"]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def apply_cards(client, path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    done = skipped = 0
    for o in data:
        c = o.get("card")
        if not c:
            skipped += 1
            continue
        cur = lib.get_source(client, o["source_id"]) or {}
        patch: dict = {}
        if not cur.get("description"):
            patch["description"] = f"{AI_MARK}{c['description']}（立場: {c['perspective']}。向く調査: {c['good_for']}）"
        if not cur.get("thematic_scope") and c["topics"]:
            patch["thematic_scope"] = "、".join(c["topics"])
        if not cur.get("geographic_scope") and c["regions"]:
            patch["geographic_scope"] = "、".join(c["regions"])
        if not cur.get("media_type") and c["media_type"] in lib.MEDIA_TYPES:
            patch["media_type"] = c["media_type"]
        if not cur.get("languages") and c["languages"]:
            patch["languages"] = c["languages"]
        if not patch:
            skipped += 1
            continue
        lib.update_source(client, o["source_id"], patch)
        geo_audit.log_audit(client, actor="source_profile_draft_apply", action="source_profile_ai_fill",
                            target_table="geo_source_registry", target_id=o["source_id"],
                            detail={"fields": sorted(patch), "confidence": c["confidence"]})
        done += 1
    print(f"反映: {done}件 / 変更なし・対象外: {skipped}件（既存の入力は上書きしていません）")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--apply", type=Path, default=None)
    ap.add_argument("--crawl-thin", action="store_true",
                    help="根拠が薄いソースだけ、サイトの公開ページを1回読んでカードを作り直す（DB書き込みなし）")
    args = ap.parse_args()
    cfg = load_config()
    client = SupabaseClient(cfg)
    if args.apply:
        apply_cards(client, args.apply)
        return
    azure_client, model = make_openai_client(cfg)
    if azure_client is None:
        sys.exit("AI分析エンジンが設定されていません")
    if args.crawl_thin:
        crawl_and_regenerate(cfg, azure_client, model)
        return
    print("ソースを読み込み中（DBは読むだけ）...")
    sources = load_sources(client, args.limit)
    print(f"{len(sources)}件。特徴カードを生成します")
    cards = make_cards(azure_client, model, sources)
    write_outputs(sources, cards)
    print(f"完了: {len(cards)}件生成 → {OUT_MD}")


if __name__ == "__main__":
    main()
