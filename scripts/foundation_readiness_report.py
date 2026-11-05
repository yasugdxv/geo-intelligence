# -*- coding: utf-8 -*-
"""Foundation 充足度レポート（読み取り専用、DBへは何も書かない）。

「その局面を、その時点の基盤で、Expert AIはどのくらい読める状態か」を、ケースごとに数値化する。Expert AIが実際に
文書を探すのと同じキーワード抽出（geo_expert_engine._extract_query_keywords）を使うので、数値は実際の検索結果に近い。

ケースの与え方: evaluation/expert_ai_blind_test/raw/TC*.json（as-of基準日と質問）。--no-asof を付けると、基準日の
絞り込みをせず「いま」の基盤を測る。

指標（ケースごと）:
  - 使える文書数・異なる出典ドメイン数・本文あり件数（公開日 <= 基準日。保存日の条件は 2 通りで報告:
    content=公開日のみ（「未来のデータは使わない」の本旨）/ strict=評価ハーネスと同じ保存日条件も）
  - 関連する知識（active）の数、関連する主体（エンティティ）数、関連する関係の数（valid_from <= 基準日）
  - 日付不明の関連文書数（検証には使えない資料）
判定: 文書(本文あり)>=5 かつ 出典ドメイン>=3 かつ 主体>=3 → READY、文書>=2 → PARTIAL、それ以外 → THIN。

使い方: python scripts/foundation_readiness_report.py [--no-asof]
結果: evaluation/FOUNDATION_READINESS_REPORT.md / .json
"""
import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import config_utils  # noqa: E402
import foundation_backfill_batch as fb  # noqa: E402
import geo_db_client  # noqa: E402
import geo_expert_engine as eng  # noqa: E402

RAW = ROOT / "evaluation" / "expert_ai_blind_test" / "raw"
OUT_MD = ROOT / "evaluation" / "FOUNDATION_READINESS_REPORT.md"
OUT_JSON = ROOT / "evaluation" / "FOUNDATION_READINESS_REPORT.json"


def d(v):
    return date.fromisoformat(str(v)[:10]) if v else None


def main():
    no_asof = "--no-asof" in sys.argv
    client = geo_db_client.SupabaseClient(config_utils.load_config())
    docs = fb.select_all(client, "geo_documents", "document_id,title,url,canonical_url,published_at,fetched_at,created_at,extracted_text,is_mock,is_demo")
    kn = fb.select_all(client, "geo_knowledge_items", "knowledge_item_id,knowledge_type,status,retrieval_status,title,summary,as_of_date,created_at")
    ents = fb.select_all(client, "geo_entities", "entity_id,canonical_name,name_en,name_ja,abbreviations")
    rels = fb.select_all(client, "geo_relationships", "relationship_id,from_entity_id,to_entity_id,valid_from,valid_to,relationship_type")
    ent_by_id = {e["entity_id"]: e for e in ents}

    expand = "--expand" in sys.argv     # 日本語キーワードを英語名にも展開して数える（クロスリンガル検索の効果測定）
    if expand:
        import geo_keyword_expansion

    def doc_hits(kws, doc, exp=None):
        blob = ((doc.get("title") or "") + " " + (doc.get("extracted_text") or "")[:4000]).lower()
        return sum(1 for k in kws if any(t.lower() in blob for t in [k] + (exp or {}).get(k, [])))

    cases = []
    for f in sorted(RAW.glob("TC[0-9][0-9].json")):
        j = json.loads(f.read_text(encoding="utf-8"))
        cases.append({"case_id": j["case_id"], "theme": j["theme"], "as_of": j["as_of_date"],
                      "query": j["input_given"]["query_text_as_sent"]})
    rows = []
    for c in cases:
        kws = eng._extract_query_keywords(c["query"])
        t0 = date.fromisoformat(c["as_of"])
        exp = geo_keyword_expansion.expand_keywords(client, kws) if expand else {}
        rel_docs = [x for x in docs if not (x.get("is_mock") or x.get("is_demo")) and doc_hits(kws, x, exp) >= 2]
        usable_content, usable_strict, undated = [], [], 0
        for x in rel_docs:
            pub = d(x.get("published_at"))
            if pub is None:
                undated += 1
                continue
            if no_asof or pub <= t0:
                usable_content.append(x)
                ing = d(x.get("fetched_at")) or d(x.get("created_at"))
                if no_asof or (ing and ing <= t0):
                    usable_strict.append(x)
        kn_rel = [k for k in kn if k["status"] == "active" and sum(1 for w in kws if w.lower() in ((k.get("title") or "") + (k.get("summary") or "")).lower()) >= 1]
        ent_rel = [e for e in ents if any(t.lower() in " ".join(filter(None, [e.get("canonical_name"), e.get("name_en"), e.get("name_ja")] + (e.get("abbreviations") or []))).lower()
                                          for w in kws for t in [w] + exp.get(w, []))]
        ent_ids = {e["entity_id"] for e in ent_rel}
        rel_rel = [r for r in rels if (r["from_entity_id"] in ent_ids or r["to_entity_id"] in ent_ids)
                   and (no_asof or not r.get("valid_from") or d(r["valid_from"]) <= t0)]
        sub = [x for x in usable_content if len(x.get("extracted_text") or "") > 500]
        domains = {urlparse(x.get("canonical_url") or x.get("url") or "").netloc for x in sub} - {""}
        verdict = "READY" if (len(sub) >= 5 and len(domains) >= 3 and len(ent_rel) >= 3) else ("PARTIAL" if len(sub) >= 2 else "THIN")
        rows.append({"case": c["case_id"], "theme": c["theme"], "as_of": c["as_of"], "keywords": kws,
                     "docs_content_rule": len(usable_content), "docs_with_text": len(sub), "docs_strict_rule": len(usable_strict),
                     "domains": len(domains), "undated_relevant_docs": undated, "active_knowledge": len(kn_rel),
                     "entities": len(ent_rel), "relationships": len(rel_rel), "verdict": verdict})
    if expand:     # 既存の出力（展開なし）を上書きしない
        global OUT_MD, OUT_JSON
        OUT_MD = OUT_MD.with_name("FOUNDATION_READINESS_REPORT_EXPANDED.md")
        OUT_JSON = OUT_JSON.with_name("FOUNDATION_READINESS_REPORT_EXPANDED.json")
    OUT_JSON.write_text(json.dumps({"no_asof": no_asof, "expand": expand, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["# Foundation 充足度レポート" + ("（基準日の絞り込みなし＝現在の基盤）" if no_asof else "（各ケースの基準日時点）"), "",
             f"全体: 文書 {len(docs)}（本文あり {sum(len(x.get('extracted_text') or '') > 500 for x in docs)}）/ 知識 {len(kn)}（active {sum(k['status'] == 'active' for k in kn)}）/ "
             f"エンティティ {len(ents)} / 関係 {len(rels)}", "",
             "| Case | テーマ | 基準日 | 文書(公開日基準) | 本文あり | 文書(保存日も) | 出典ドメイン | 日付不明 | 知識active | 主体 | 関係 | 判定 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['case']} | {r['theme'][:22]} | {r['as_of']} | {r['docs_content_rule']} | {r['docs_with_text']} | {r['docs_strict_rule']} | "
                     f"{r['domains']} | {r['undated_relevant_docs']} | {r['active_knowledge']} | {r['entities']} | {r['relationships']} | **{r['verdict']}** |")
    cnt = {}
    for r in rows:
        cnt[r["verdict"]] = cnt.get(r["verdict"], 0) + 1
    lines += ["", f"判定の内訳: {cnt}", "", "- 文書(公開日基準): 「未来のデータは使わない」の本旨（記事の公開日 <= 基準日）。文書(保存日も): 評価ハーネスと同じ、DBへ保存した日も基準日以前を要求する厳格条件。",
              "- 日付不明: 関連するが公開日が空で、検証には使えない文書。"]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
