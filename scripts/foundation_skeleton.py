# -*- coding: utf-8 -*-
"""Foundation強化 段階2: 骨格（エンティティと時点付きの構造的な事実の関係）の構築。

人のチェックは入れず、AI/機械による多段検証で品質を担保する。既定は dry-run（DBを書き換えない）、--live で書き込む。

【エンティティ】config/geo_foundation_skeleton_seed.py の名前リストを、Wikidataで裏付けてから登録する。
  ゲート: (1) 検索の最上位候補の英語ラベル/別名が、種に書いたWikidata英語ラベルと一致すること
          (2) 曖昧さ回避ページ・一致しない候補は登録しない（理由を記録）
          (3) 既存エンティティ（名前またはWikidata ID一致）は重複登録せず再利用する
  登録内容: canonical_name / name_en / name_ja（Wikidataラベル）/ 略称 / 国コード(P297) /
            親（国）への parent_entity_id / external_ids.wikidata / 別名（ja・zh・Wikidata別名、geo_entity_aliases）
【関係】
  (a) 国際機関への加盟: Wikidata P463（開始日P580・終了日P582つき）から、種のエンティティ間だけを生成。
      終了済みの加盟は valid_to つきで残す（as-of検証で使える）。
  (b) 二国間の条約・同盟: 種の事実を、Perplexity(出典つきWeb検索)→AI判定を独立に2回行い、
      2回とも「事実と日付が出典で裏付けられた」ものだけ登録する。1回でも矛盾/不明なら登録しない。
  関係には formal_basis（根拠）と confidence を付ける。DBスキーマ変更なし。

使い方:  python scripts/foundation_skeleton.py [--live] [--skip-verify]
結果: evaluation/FOUNDATION_SKELETON_RESULTS.json / 検証キャッシュ evaluation/FOUNDATION_SKELETON_VERIFICATION_CACHE.json
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "config"))

import config_utils  # noqa: E402
import geo_db_client  # noqa: E402
import geo_foundation_skeleton_seed as seed  # noqa: E402

OUT = ROOT / "evaluation" / "FOUNDATION_SKELETON_RESULTS.json"
VCACHE = ROOT / "evaluation" / "FOUNDATION_SKELETON_VERIFICATION_CACHE.json"
UA = {"User-Agent": "geo-foundation-skeleton/1.0 (internal research; contact: admin)"}
WD_API = "https://www.wikidata.org/w/api.php"
WD_SPARQL = "https://query.wikidata.org/sparql"
ACTOR = "ai:foundation_skeleton_v1"
EXPECTED_P31_BLOCK = {"Q4167410", "Q4167836", "Q13406463"}  # 曖昧さ回避・カテゴリ・一覧記事


class Wikidata:
    def __init__(self, proxies, verify):
        self.proxies, self.verify = proxies, verify

    def _get(self, url, params):
        last = None
        for attempt in range(5):
            try:
                r = requests.get(url, params=params, headers=UA, proxies=self.proxies, verify=self.verify, timeout=40)
                if r.status_code == 429:
                    last = "429 rate limited"
                    time.sleep(10 * (attempt + 1))
                    continue
                r.raise_for_status()
                time.sleep(0.4)  # 続けて叩かない（429予防）
                return r.json()
            except requests.RequestException as e:
                last = f"{type(e).__name__}"
                time.sleep(3 * (attempt + 1))
        raise RuntimeError(f"wikidata request failed after retries: {last}")

    def search(self, label):
        d = self._get(WD_API, {"action": "wbsearchentities", "search": label, "language": "en", "format": "json", "limit": 5})
        return d.get("search", [])

    def entity(self, qid):
        d = self._get(WD_API, {"action": "wbgetentities", "ids": qid, "format": "json",
                               "props": "labels|aliases|descriptions|claims", "languages": "en|ja|zh"})
        return d["entities"][qid]

    def sparql(self, query):
        for attempt in range(3):
            r = requests.post(WD_SPARQL, data={"query": query, "format": "json"}, headers=UA,
                              proxies=self.proxies, verify=self.verify, timeout=90)
            if r.status_code == 429:
                time.sleep(8 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()["results"]["bindings"]
        raise RuntimeError("sparql rate-limited")


def label(ent, lang):
    return ((ent.get("labels") or {}).get(lang) or {}).get("value")


def claim_values(ent, prop):
    out = []
    for c in (ent.get("claims") or {}).get(prop, []):
        v = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        out.append(v)
    return out


def resolve_seed(wd, canonical, wd_label):
    """Wikidataで裏付ける。戻り値: dict(ok, qid, ent, reason)。"""
    hits = wd.search(wd_label)
    norm = lambda x: (x or "").replace("’", "'").casefold()  # Wikidataのアポストロフィ(’)を正規化して比較
    want = norm(wd_label)
    for h in hits:
        if (h.get("description") or "").lower().startswith("wikimedia disambiguation"):
            continue
        ent = wd.entity(h["id"])
        p31 = {v.get("id") for v in claim_values(ent, "P31") if isinstance(v, dict)}
        if p31 & EXPECTED_P31_BLOCK:
            continue
        names = {norm(label(ent, "en"))}
        names |= {norm(a.get("value")) for a in (ent.get("aliases") or {}).get("en", [])}
        if want in names:
            return {"ok": True, "qid": h["id"], "ent": ent, "description": h.get("description")}
        time.sleep(0.2)
    return {"ok": False, "reason": "no_label_match", "candidates": [(h["id"], h.get("label"), h.get("description")) for h in hits[:3]]}


def build_seed_rows():
    rows = []
    for c in seed.COUNTRIES:
        canon = seed.COUNTRY_CANONICAL.get(c, c)
        etype = "country"
        rows.append({"canonical": canon, "wd_label": c, "type": etype, "parent": None, "abbr": [], "kind": "country"})
    for canon, wdl, domain, abbr in seed.ORGS:
        rows.append({"canonical": canon, "wd_label": wdl, "type": "international_organization", "parent": None,
                     "abbr": abbr, "kind": "org", "domain": domain})
    for canon, wdl, etype, parent, abbr in seed.GOVERNMENT_BODIES:
        rows.append({"canonical": canon, "wd_label": wdl, "type": etype, "parent": parent, "abbr": abbr, "kind": "gov"})
    for canon, wdl, etype, parent in seed.COMPANIES:
        rows.append({"canonical": canon, "wd_label": wdl, "type": etype, "parent": parent, "abbr": [], "kind": "company"})
    for canon, wdl, etype in seed.LOCATIONS:
        rows.append({"canonical": canon, "wd_label": wdl, "type": etype, "parent": None, "abbr": [], "kind": "location"})
    return rows


def existing_index(client):
    rows, off = [], 0
    while True:
        got = client.select("geo_entities", {"select": "entity_id,canonical_name,name_en,external_ids,entity_type,country_code",
                                             "limit": "500", "offset": str(off)})
        rows += got
        if len(got) < 500:
            break
        off += 500
    by_name, by_qid = {}, {}
    for r in rows:
        for n in (r.get("canonical_name"), r.get("name_en")):
            if n:
                by_name[n.casefold()] = r
                by_name[n.split(" (")[0].casefold()] = r
        q = (r.get("external_ids") or {}).get("wikidata")
        if q:
            by_qid[q] = r
    return by_name, by_qid


def phase_entities(client, wd, live, results):
    by_name, by_qid = existing_index(client)
    resolved = {}  # canonical -> {"qid", "entity_id" or None}
    country_ids = {}
    out = results["entities"] = []
    for row in build_seed_rows():
        item = {"canonical": row["canonical"], "type": row["type"]}
        print(f"  entity {len(out) + 1}: {row['canonical']}", flush=True)
        try:
            res = resolve_seed(wd, row["canonical"], row["wd_label"])
        except Exception as e:  # noqa: BLE001
            item.update(status="error", reason=f"{type(e).__name__}: {str(e)[:80]}")
            out.append(item)
            continue
        if not res["ok"]:
            item.update(status="skipped_unverified", reason=res["reason"], candidates=res.get("candidates"))
            out.append(item)
            continue
        ent, qid = res["ent"], res["qid"]
        existing = by_qid.get(qid) or by_name.get(row["canonical"].casefold()) or by_name.get(row["wd_label"].casefold())
        item["wikidata"] = qid
        resolved[row["canonical"]] = {"qid": qid, "entity_id": existing["entity_id"] if existing else None, "row": row}
        if existing:
            item.update(status="exists", entity_id=existing["entity_id"])
            if row["kind"] == "country":
                country_ids[row["canonical"]] = existing["entity_id"]
            out.append(item)
            continue
        iso = [v for v in claim_values(ent, "P297") if isinstance(v, str)]
        record = {
            "entity_type": row["type"], "canonical_name": row["canonical"], "name_en": label(ent, "en") or row["wd_label"],
            "name_ja": label(ent, "ja"), "local_name": label(ent, "zh") if row["canonical"] in ("China", "Taiwan") else None,
            "abbreviations": row["abbr"], "country_code": iso[0] if iso and row["kind"] == "country" else None,
            "external_ids": {"wikidata": qid, "source": "foundation_skeleton_v1"}, "is_demo": False,
        }
        item.update(status="to_create" if not live else "created", name_ja=record["name_ja"], country_code=record["country_code"])
        aliases = [(a, "abbreviation", None) for a in row["abbr"]]
        for lang, atype in (("ja", "local_language"), ("zh", "local_language")):
            lv = label(ent, lang)
            if lv and lv != record["canonical_name"]:
                aliases.append((lv, atype, lang))
        for a in (ent.get("aliases") or {}).get("en", [])[:4]:
            if a.get("value") and a["value"].casefold() != record["canonical_name"].casefold():
                aliases.append((a["value"], "former_name" if False else "transliteration", "en"))
        if live:
            created = client.insert("geo_entities", [record])[0]
            resolved[row["canonical"]]["entity_id"] = created["entity_id"]
            item["entity_id"] = created["entity_id"]
            seen = set()
            alias_rows = []
            for text, atype, lang in aliases:
                if text.casefold() in seen:
                    continue
                seen.add(text.casefold())
                alias_rows.append({"entity_id": created["entity_id"], "alias_text": text, "alias_type": atype, "language": lang})
            if alias_rows:
                client.insert("geo_entity_aliases", alias_rows)
            if row["kind"] == "country":
                country_ids[row["canonical"]] = created["entity_id"]
        out.append(item)
        time.sleep(0.2)
    # 親（国）の紐付け
    if live:
        for canon, info in resolved.items():
            parent = info["row"].get("parent")
            if parent and info.get("entity_id"):
                pid = (resolved.get(parent) or {}).get("entity_id")
                if pid:
                    client.update("geo_entities", {"entity_id": f"eq.{info['entity_id']}", "parent_entity_id": "is.null"},
                                  {"parent_entity_id": pid})
    results["_resolved"] = resolved
    return resolved


def phase_memberships(client, wd, live, results, resolved):
    qid_to = {v["qid"]: (k, v) for k, v in resolved.items()}
    members = [q for q, (k, v) in qid_to.items() if v["row"]["kind"] == "country"]
    orgs = [q for q, (k, v) in qid_to.items() if v["row"]["kind"] == "org"]
    q = ("SELECT ?s ?o ?start ?end WHERE { VALUES ?s { %s } VALUES ?o { %s } ?s p:P463 ?st . ?st ps:P463 ?o . "
         "OPTIONAL { ?st pq:P580 ?start } OPTIONAL { ?st pq:P582 ?end } }") % (
        " ".join(f"wd:{x}" for x in members), " ".join(f"wd:{x}" for x in orgs))
    bindings = wd.sparql(q)
    existing = {(r["from_entity_id"], r["to_entity_id"], r["relationship_type"], r.get("valid_from"), r.get("valid_to")) for r in
                client.select("geo_relationships", {"select": "from_entity_id,to_entity_id,relationship_type,valid_from,valid_to", "limit": "5000"})}
    out = results["memberships"] = []
    seen = set()
    for b in bindings:
        sq, oq = b["s"]["value"].rsplit("/", 1)[1], b["o"]["value"].rsplit("/", 1)[1]
        s_name, s_info = qid_to[sq]
        o_name, o_info = qid_to[oq]
        start = (b.get("start") or {}).get("value", "")[:10] or None
        end = (b.get("end") or {}).get("value", "")[:10] or None
        key = (s_name, o_name, start, end)
        if key in seen:
            continue
        seen.add(key)
        item = {"from": s_name, "to": o_name, "valid_from": start, "valid_to": end,
                "domain": o_info["row"].get("domain", "diplomacy")}
        sid, oid = s_info.get("entity_id"), o_info.get("entity_id")
        if live and sid and oid:
            if (sid, oid, "member_of", start, end) in existing:
                item["status"] = "exists"
            else:
                client.insert("geo_relationships", [{
                    "from_entity_id": sid, "to_entity_id": oid, "relationship_domain": item["domain"],
                    "relationship_type": "member_of", "description": f"{s_name} は {o_name} の加盟国（Wikidata P463）",
                    "formal_basis": f"Wikidata:P463 {sq}->{oq}", "valid_from": start, "valid_to": end,
                    "confidence": "高" if start else "中", "dependency_direction": "none", "is_demo": False}])
                existing.add((sid, oid, "member_of", start, end))
                item["status"] = "created"
        else:
            item["status"] = "to_create" if not live else "skipped_no_entity_id"
        out.append(item)


def load_vcache():
    return json.loads(VCACHE.read_text(encoding="utf-8")) if VCACHE.exists() else {}


VERDICT_SCHEMA = {"type": "object", "properties": {
    "verdict": {"type": "string", "enum": ["supported", "contradicted", "unclear"]},
    "date_matches": {"type": "boolean"}, "found_date": {"type": ["string", "null"]},
    "reason": {"type": "string"}}, "required": ["verdict", "date_matches", "found_date", "reason"], "additionalProperties": False}


def verify_fact(fact, settings, azure, model, vcache):
    import geo_llm_common
    import geo_perplexity_search as pplx
    fid = fact[0]
    if fid in vcache:
        return vcache[fid]
    _, frm, to, domain, rtype, basis, vfrom, question, mutual = fact
    runs = []
    for i in range(2):  # 独立に2回（検索から別々に行う）
        try:
            r = pplx.search(settings, question=f"{question} 条約名: {basis}。{frm}と{to}。正確な日付と出典を挙げてください。",
                            instruction="必ず日付（年月日）と一次/信頼できる出典を示すこと。")
            sources = [{"url": c["url"], "title": c.get("title")} for c in r["citations"]][:6]
            judged = geo_llm_common.call_llm_structured(
                azure, model,
                "あなたは事実確認担当です。与えられた検索要約と出典タイトルだけを根拠に、主張を判定します。要約に無い事実は補わない。",
                f"# 主張\n{frm}と{to}の間の「{basis}」が {vfrom} に締結/発効（または制定）した。\n"
                f"# 検索要約\n{(r['summary'] or '')[:2500]}\n# 出典\n" + "\n".join(f"- {s['title']} {s['url']}" for s in sources) +
                "\n\n verdict: 主張の事実関係が要約で裏付けられれば supported、矛盾すれば contradicted、判断できなければ unclear。"
                "date_matches: 主張の日付（署名日または発効日のどちらかで一致すればtrue）が要約と整合するか。found_date: 要約にある日付。",
                VERDICT_SCHEMA, "foundation_skeleton_fact_check")["data"]
            runs.append({"verdict": judged["verdict"], "date_matches": judged["date_matches"], "found_date": judged["found_date"],
                         "reason": judged["reason"][:240], "n_sources": len(sources)})
        except Exception as e:  # noqa: BLE001
            runs.append({"verdict": "error", "date_matches": False, "found_date": None, "reason": f"{type(e).__name__}: {str(e)[:100]}", "n_sources": 0})
    ok = all(r["verdict"] == "supported" and r["date_matches"] and r["n_sources"] >= 2 for r in runs)
    vcache[fid] = {"passed": ok, "runs": runs, "claimed_date": vfrom}
    VCACHE.write_text(json.dumps(vcache, ensure_ascii=False, indent=1), encoding="utf-8")
    return vcache[fid]


def phase_bilateral(client, live, results, resolved, skip_verify):
    out = results["bilateral"] = []
    vcache = load_vcache()
    settings = azure = model = None
    if not skip_verify:
        import geo_perplexity_search as pplx
        from ai_client import make_openai_client
        settings = pplx.get_settings()
        azure, model = make_openai_client(config_utils.load_config())
        if not settings or not azure:
            raise SystemExit("Perplexity/Azureの設定が無いため検証できません（--skip-verifyで検証なし）")
    existing = {(r["from_entity_id"], r["to_entity_id"], r["relationship_type"], r.get("valid_from"), r.get("valid_to")) for r in
                client.select("geo_relationships", {"select": "from_entity_id,to_entity_id,relationship_type,valid_from,valid_to", "limit": "5000"})}
    for fact in seed.BILATERAL_FACTS:
        fid, frm, to, domain, rtype, basis, vfrom, question, mutual = fact
        item = {"id": fid, "from": frm, "to": to, "type": rtype, "valid_from": vfrom}
        a, b = (resolved.get(frm) or {}).get("entity_id"), (resolved.get(to) or {}).get("entity_id")
        if not skip_verify:
            v = verify_fact(fact, settings, azure, model, vcache)
            item["verification"] = v
            if not v["passed"]:
                item["status"] = "rejected_by_verification"
                out.append(item)
                continue
        else:
            item["verification"] = "skipped"
        if not (a and b):
            item["status"] = "to_create" if not live else "skipped_no_entity_id"
        elif (a, b, rtype, vfrom, None) in existing:
            item["status"] = "exists"
        elif live:
            client.insert("geo_relationships", [{
                "from_entity_id": a, "to_entity_id": b, "relationship_domain": domain, "relationship_type": rtype,
                "description": f"{frm}–{to}: {basis}", "formal_basis": basis, "valid_from": vfrom,
                "leverage_direction": "mutual" if mutual else None, "dependency_direction": "mutual" if mutual else None,
                "confidence": "高", "is_demo": False}])
            existing.add((a, b, rtype, vfrom, None))
            item["status"] = "created"
        else:
            item["status"] = "to_create"
        out.append(item)


def main():
    args = sys.argv[1:]
    live, skip_verify = "--live" in args, "--skip-verify" in args
    cfg = config_utils.load_config()
    client = geo_db_client.SupabaseClient(cfg)
    wd = Wikidata(config_utils.make_proxies(cfg), cfg.get("ssl", {}).get("verify", True))
    results = {"mode": "live" if live else "dry_run", "started_at": datetime.now(timezone.utc).isoformat()}
    print(f"mode={'LIVE' if live else 'DRY-RUN'}", flush=True)
    resolved = phase_entities(client, wd, live, results)
    c = {}
    for e in results["entities"]:
        c[e["status"]] = c.get(e["status"], 0) + 1
    print("entities:", json.dumps(c, ensure_ascii=False), flush=True)
    phase_memberships(client, wd, live, results, resolved)
    c = {}
    for m in results["memberships"]:
        c[m["status"]] = c.get(m["status"], 0) + 1
    print("memberships:", json.dumps(c, ensure_ascii=False), flush=True)
    phase_bilateral(client, live, results, resolved, skip_verify)
    c = {}
    for m in results["bilateral"]:
        c[m["status"]] = c.get(m["status"], 0) + 1
    print("bilateral:", json.dumps(c, ensure_ascii=False), flush=True)
    results.pop("_resolved", None)
    results["finished_at"] = datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("results:", OUT, flush=True)


if __name__ == "__main__":
    main()
