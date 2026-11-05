# -*- coding: utf-8 -*-
"""Foundation 段階3: 過去・近年の公的資料を、出典つきWeb検索で見つけて geo_documents へ取り込む（公的ドメインのみ）。

流れ（既定は dry-run＝DBを書き換えない。--live で登録）:
  1. config/geo_foundation_history_queries.py のクエリごとに、Perplexity（公的ドメインに限定）で実在URLを探す。
  2. ゲート（全て通ったものだけ登録）:
       - 公的ドメイン（foundation_backfill_batch.PUBLIC_DOMAIN_PATTERNS）であること
       - 既存の文書とURL/内容(content_hash)が重複しないこと
       - 本文を取得できて 400字以上・40万字以下であること（記事ではない巨大データ・トップページは除外）
       - 公開日: 検索結果の日付→無ければページのメタ日付。信用できない日付（1/1固定・2015年より前・未来）は
         空のまま（as-of検証では使わない文書として保持）
       - AIの関連性判定: クエリが探している資料と無関係なページは登録しない（Azure、短い判定）
  3. 登録は research_origin="history_ingest:<領域>"、受け皿テーマ foundation_public_sources、来歴を response_headers へ。

使い方:  python scripts/foundation_history_ingest.py [--live] [--limit-queries N] [--skip-ai]
結果: evaluation/FOUNDATION_HISTORY_INGEST_RESULTS.json
"""
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "config"))
sys.path.insert(0, str(ROOT / "scripts"))

import config_utils  # noqa: E402
import foundation_backfill_batch as fb  # noqa: E402
import geo_db_client  # noqa: E402
import geo_documents_store as store  # noqa: E402
import geo_foundation_history_queries as hq  # noqa: E402
import geo_llm_common  # noqa: E402
import geo_perplexity_search as pplx  # noqa: E402
from ai_client import make_openai_client  # noqa: E402

OUT = ROOT / "evaluation" / "FOUNDATION_HISTORY_INGEST_RESULTS.json"
CASE_QUERY_CACHE = ROOT / "evaluation" / "FOUNDATION_CASE_QUERIES.json"
RAW_CASES = ROOT / "evaluation" / "expert_ai_blind_test" / "raw"
THEME_CODE = "foundation_public_sources"
MIN_TEXT, MAX_TEXT = 400, 400_000
FETCH_DELAY = 1.0
MAX_PER_QUERY = 10

REL_SCHEMA = {"type": "object", "properties": {
    "relevant": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["relevant", "reason"], "additionalProperties": False}


def ai_relevant(azure, model, query, title, text):
    r = geo_llm_common.call_llm_structured(
        azure, model,
        "あなたは資料の関連性を判定する担当です。調査の話題（トピック）に対して、そのページが公的な一次資料として"
        "実質的な内容を持つかを判断します。探している文書そのものである必要はありません（同じ話題を扱う別の公式声明・"
        "報告書・白書・条約文・記者会見録・報道発表も有用）。",
        f"# 調査の話題\n{query}\n\n# 取得したページ\nタイトル: {title}\n冒頭: {text[:900]}\n\n"
        "relevant: 話題に関係する政府・国際機関などの公式な発表・文書として、実質的な内容があれば true。話題と無関係、"
        "エラー/ログイン画面、目次・一覧のみ、判読できないデータ断片、広告的なページなら false。",
        REL_SCHEMA, "foundation_history_relevance_v2")["data"]
    return r["relevant"], r["reason"][:160]


QUERY_GEN_SCHEMA = {"type": "object", "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
                    "required": ["queries"], "additionalProperties": False}


def case_queries(azure, model, per_case=4, round_no=1, only_ids=None):
    """20ケースの質問から、そのケースを基準日時点で読むために要る「公的資料」を探す検索クエリを、AIに作らせる。
    作らせるのはクエリ（探す資料の種類）だけで、事実・結論・予測は書かせない。結果はキャッシュして再利用する。"""
    cache = json.loads(CASE_QUERY_CACHE.read_text(encoding="utf-8")) if CASE_QUERY_CACHE.exists() else {}
    out = []
    for f in sorted(RAW_CASES.glob("TC[0-9][0-9].json")):
        j = json.loads(f.read_text(encoding="utf-8"))
        cid0 = j["case_id"]
        if only_ids and cid0 not in only_ids:
            continue
        cid = cid0 if round_no == 1 else f"{cid0}#{round_no}"
        previous = [q for k, v in cache.items() if k.split("#")[0] == cid0 and k != cid for q in v["queries"]]
        if cid not in cache:
            extra = ("\n\n# すでに使った検索クエリ（これらと同じ内容・言い換えは作らない。まだ探せていない側面を探す）\n"
                     + "\n".join(f"- {q}" for q in previous)) if previous else ""
            r = geo_llm_common.call_llm_structured(
                azure, model,
                "あなたは調査設計の担当です。与えられた地政学ケースを基準日時点で分析するために必要な『公的な一次資料』"
                "（政府・国際機関・議会・公的機関の声明、報告書、白書、条約文、記者会見録、統計）を、Web検索で見つけるための"
                "検索クエリを作ります。クエリは資料の種類・主体・時期・話題だけを書き、事実や結論や予測は書かない。"
                "各クエリは80文字以内。日本語と英語を混ぜてよい。基準日より前に公表された資料を探す形にする。",
                f"# ケース\nテーマ: {j['theme']}\n基準日: {j['as_of_date']}\n状況: {j['input_given'].get('situation')}\n"
                f"背景: {j['input_given'].get('background')}\n問い: {j['input_given'].get('question')}\n\n"
                f"queries: {per_case}件。互いに違う側面（関係する主体の公式立場／直近の動きの公式文書／制度・条約・枠組み／"
                "数値・統計の公的資料）を探すクエリにする。" + extra,
                QUERY_GEN_SCHEMA, "foundation_case_queries")["data"]
            cache[cid] = {"theme": j["theme"], "as_of": j["as_of_date"], "queries": [q[:120] for q in r["queries"]][:per_case]}
            CASE_QUERY_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        for q in cache[cid]["queries"]:
            out.append((f"case_{cid}", f"{q}（{cache[cid]['as_of']}までに公表された資料）"))
    return out


def page_date_or_none(value):
    d = fb.trustworthy_date(value)
    if d and d > date.today().isoformat():
        return None
    return d


def main():
    args = sys.argv[1:]
    live, skip_ai = "--live" in args, "--skip-ai" in args
    with_cases, only_cases = "--case-queries" in args, "--only-case-queries" in args
    nq = int(args[args.index("--limit-queries") + 1]) if "--limit-queries" in args else None
    cfg = config_utils.load_config()
    client = geo_db_client.SupabaseClient(cfg)
    proxies, verify = config_utils.make_proxies(cfg), cfg.get("ssl", {}).get("verify", True)
    settings = pplx.get_settings()
    azure, model = make_openai_client(cfg)
    if not settings or (not skip_ai and not azure):
        raise SystemExit("Perplexity/Azureの設定が無いため実行できません")

    theme = client.select("geo_themes", {"select": "theme_id", "code": f"eq.{THEME_CODE}", "limit": "1"})
    if not theme:
        raise SystemExit(f"受け皿テーマ {THEME_CODE} がありません（段階1で作成済みのはずです）")
    theme_id = theme[0]["theme_id"]
    existing = fb.select_all(client, "geo_documents", "canonical_url,url,content_hash")
    known_urls = {u for d in existing for u in (d.get("canonical_url"), d.get("url")) if u}
    known_hashes = {d["content_hash"] for d in existing if d.get("content_hash")}

    queries = [] if only_cases else list(hq.QUERIES)
    if with_cases or only_cases:
        round_no = int(args[args.index("--case-round") + 1]) if "--case-round" in args else 1
        only_ids = set(args[args.index("--case-ids") + 1].split(",")) if "--case-ids" in args else None
        queries += case_queries(azure, model, per_case=6 if round_no > 1 else 4, round_no=round_no, only_ids=only_ids)
    if nq:
        queries = queries[:nq]
    results = {"mode": "live" if live else "dry_run", "started_at": datetime.now(timezone.utc).isoformat(),
               "queries": len(queries), "items": []}
    seen_urls = set()
    for qi, (dkey, query) in enumerate(queries, 1):
        cites = []
        for domains in (hq.PUBLIC_DOMAINS, hq.PUBLIC_DOMAINS_B):
            try:
                r = pplx.search(settings, question=query, domains=domains,
                                instruction="公式文書そのもの（声明・白書・条約・報告書）のURLを優先して示すこと。")
                cites += r["citations"]
            except Exception as e:  # noqa: BLE001
                results["items"].append({"query": query, "status": "search_error", "reason": f"{type(e).__name__}: {str(e)[:80]}"})
        print(f"[{qi}/{len(queries)}] {dkey}: {query[:50]} -> {len(cites)} citations", flush=True)
        # 公的ドメインのものだけを、1クエリあたり最大 MAX_PER_QUERY 件まで取り込み対象にする（量と時間の上限）
        cites = [c for c in cites if fb.is_public(c["url"])][:MAX_PER_QUERY]
        for c in cites:
            url = c["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            item = {"domain_key": dkey, "query": query, "url": url, "title": (c.get("title") or "")[:80],
                    "domain": urlparse(url).netloc}
            if not fb.is_public(url):
                item.update(status="skipped", reason="non_public_domain")
            elif url in known_urls:
                item.update(status="skipped", reason="url_already_in_documents")
            else:
                try:
                    f = store.fetch_url_for_backfill(url, proxies=proxies, verify=verify)
                    text = (f.get("text") or "") if f["ok"] else ""
                    if not f["ok"]:
                        item.update(status="fetch_failed", reason=str(f.get("http_status") or f.get("error"))[:60])
                    elif len(text) < MIN_TEXT or store._is_stub_text(c.get("title"), text):
                        item.update(status="rejected", reason="text_too_short", length=len(text))
                    elif len(text) > MAX_TEXT:
                        item.update(status="rejected", reason="text_too_large", length=len(text))
                    elif f["content_hash"] in known_hashes:
                        item.update(status="rejected", reason="duplicate_content_hash")
                    else:
                        title = f.get("title") or c.get("title") or ""
                        ok_rel, why = (True, "skipped") if skip_ai else ai_relevant(azure, model, query, title, text)
                        pub_meta = page_date_or_none(c.get("published_at"))
                        date_source = "search_metadata" if pub_meta else None
                        if not pub_meta:
                            pub_meta = page_date_or_none(f.get("published_at"))
                            date_source = "page_metadata" if pub_meta else "none"
                        if pub_meta and not fb.date_consistent_with_text(pub_meta, text):
                            # 本文がもっと後の日付に言及している＝公開日はサイトのテンプレート日付等の可能性が高い→空にする
                            # （誤って早い日付だと、as-of検証で後の資料が過去として混ざるため）
                            date_source = "rejected_inconsistent_with_text"
                            pub_meta = None
                        if not ok_rel:
                            item.update(status="rejected", reason=f"ai_irrelevant: {why}")
                        else:
                            item.update(status="dry_run" if not live else "ingested", length=len(text),
                                        published_at=pub_meta, date_source=date_source)
                            if live:
                                row = {"theme_id": theme_id, "source_id": None, "title": title[:300], "url": url,
                                       "canonical_url": url, "published_at": pub_meta, "language": None,
                                       "content_hash": f["content_hash"], "mime_type": "text/html", "extracted_text": text,
                                       "fetch_status": "成功", "http_status": f.get("http_status"),
                                       "parser_version": store.PARSER_VERSION, "is_mock": False,
                                       "research_origin": f"history_ingest:{dkey}",
                                       "response_headers": {"history_ingest": {
                                           "query": query, "domain_key": dkey, "date_source": date_source,
                                           "ingested_at": datetime.now(timezone.utc).isoformat()}}}
                                doc = client.insert("geo_documents", [row])[0]
                                item["document_id"] = doc["document_id"]
                                known_urls.add(url)
                                known_hashes.add(f["content_hash"])
                except Exception as e:  # noqa: BLE001
                    item.update(status=f"error:{type(e).__name__}", reason=str(e)[:100])
            results["items"].append(item)
            time.sleep(FETCH_DELAY)
        OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    results["finished_at"] = datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = {}
    for it in results["items"]:
        k = it["status"] + (":" + it["reason"][:30] if it["status"] in ("skipped", "rejected") and it.get("reason") else "")
        summary[k] = summary.get(k, 0) + 1
    print("summary:", json.dumps(summary, ensure_ascii=False), "\nresults:", OUT, flush=True)


if __name__ == "__main__":
    main()
