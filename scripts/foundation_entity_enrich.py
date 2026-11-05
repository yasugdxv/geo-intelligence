# -*- coding: utf-8 -*-
"""Foundation: エンティティの日本語名・別名の整備と、検証事例に出てくる人物の追加（Wikidataで裏付け）。

目的: Expert AIの検索は質問文のキーワードで文書を探す。質問が日本語（「トランプ」「ネタニヤフ」「ホルムズ海峡」）で、
公的資料が英語（Trump / Netanyahu / Strait of Hormuz）だと一致しない。エンティティに日本語名・別名・略称を持たせ、
検索側（geo_keyword_expansion）が日本語名→英語名へ展開できるようにする。
やること（既定は dry-run、--live で書き込み。既存の値は上書きせず、足りないものだけ補う）:
  1. 既存エンティティのうち、日本語名/別名が足りないものを補う（Wikidataの日本語ラベル・別名、姓だけの形、
     日本語ニュースの通称（米国・中国・北朝鮮など。下の SHORT_FORMS））。
  2. 人物エンティティを追加する（種の名前→Wikidataで「人間(Q5)」かつ英語ラベル/別名が一致するものだけ。人物は
     時点で役職が変わるため、役職・立場は登録しない＝同定のための名前だけ）。
使い方:  python scripts/foundation_entity_enrich.py [--live]
結果: evaluation/FOUNDATION_ENTITY_ENRICH_RESULTS.json
"""
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import config_utils  # noqa: E402
import foundation_skeleton as sk  # noqa: E402
import geo_db_client  # noqa: E402

OUT = ROOT / "evaluation" / "FOUNDATION_ENTITY_ENRICH_RESULTS.json"

# 日本語ニュースで一般的な通称（Wikidataの正式名称と違うもの）。事実ではなく表記の対応のみ。
SHORT_FORMS = {
    "United States": ["米国", "アメリカ"], "China": ["中国"], "Japan": ["日本"], "Russia": ["ロシア"],
    "Taiwan": ["台湾"], "South Korea": ["韓国"], "North Korea": ["北朝鮮"], "United Kingdom": ["英国", "イギリス"],
    "Germany": ["ドイツ"], "France": ["フランス"], "Iran": ["イラン"], "Israel": ["イスラエル"],
    "Saudi Arabia": ["サウジ", "サウジアラビア"], "United Arab Emirates": ["UAE", "アラブ首長国連邦"],
    "Ukraine": ["ウクライナ"], "Venezuela": ["ベネズエラ"], "Canada": ["カナダ"], "Spain": ["スペイン"],
    "India": ["インド"], "Australia": ["豪州", "オーストラリア"], "Türkiye": ["トルコ"], "Philippines": ["フィリピン"],
    "European Union": ["EU", "欧州連合"], "North Atlantic Treaty Organization": ["NATO", "北大西洋条約機構"],
    "United States Congress": ["米議会", "連邦議会"], "People's Liberation Army": ["人民解放軍", "PLA"],
    "Islamic Revolutionary Guard Corps (IRGC)": ["革命防衛隊", "IRGC"], "Strait of Hormuz": ["ホルムズ海峡", "ホルムズ"],
    "Taiwan Strait": ["台湾海峡"], "Senkaku Islands": ["尖閣諸島", "尖閣"], "Bureau of Industry and Security (BIS)": ["BIS", "商務省産業安全保障局"],
}

# 追加する人物（名前、Wikidata英語ラベル）。検証事例（TC01〜TC20）と主要な米・中・日・中東の関係者を中心に、
# 同定のための名前だけを登録する。
PERSONS = [
    ("JD Vance", "JD Vance"), ("Marco Rubio", "Marco Rubio"), ("Pete Hegseth", "Pete Hegseth"),
    ("Scott Bessent", "Scott Bessent"), ("Howard Lutnick", "Howard Lutnick"), ("Steve Witkoff", "Steve Witkoff"),
    ("Wang Yi", "Wang Yi"), ("Shigeru Ishiba", "Shigeru Ishiba"), ("Ali Khamenei", "Ali Khamenei"),
    ("Masoud Pezeshkian", "Masoud Pezeshkian"), ("Abbas Araghchi", "Abbas Araghchi"), ("Reza Pahlavi", "Reza Pahlavi"),
    ("Nicolás Maduro", "Nicolás Maduro"), ("Delcy Rodríguez", "Delcy Rodríguez"), ("María Corina Machado", "María Corina Machado"),
    ("Mark Carney", "Mark Carney"), ("Danielle Smith", "Danielle Smith"), ("Pedro Sánchez", "Pedro Sánchez"),
    ("Lee Jae-myung", "Lee Jae-myung"), ("Lai Ching-te", "Lai Ching-te"), ("Narendra Modi", "Narendra Modi"),
    ("Recep Tayyip Erdoğan", "Recep Tayyip Erdoğan"), ("Keir Starmer", "Keir Starmer"), ("Giorgia Meloni", "Giorgia Meloni"),
    ("Donald Tusk", "Donald Tusk"), ("Viktor Orbán", "Viktor Orbán"), ("Ursula von der Leyen", "Ursula von der Leyen"),
    ("Mark Rutte", "Mark Rutte"), ("Antonio Guterres", "António Guterres"), ("Rafael Grossi", "Rafael Grossi"),
    ("Sergey Lavrov", "Sergey Lavrov"), ("Andriy Yermak", "Andriy Yermak"), ("Naftali Bennett", "Naftali Bennett"),
    ("Yoav Gallant", "Yoav Gallant"), ("Itamar Ben-Gvir", "Itamar Ben-Gvir"), ("Hakeem Jeffries", "Hakeem Jeffries"),
    ("Mike Johnson", "Mike Johnson"),
]


def derive_ja_aliases(ent, is_person=False) -> list:
    """Wikidataの日本語ラベル/別名から、検索で使う形を作る。「ドナルド・トランプ」→「トランプ」（姓）も加える。
    誤検索を避けるため、1文字・括弧付きは除外し、人物以外は別名を1つまでにする（旧称などの混入を防ぐ）。"""
    out = []
    ja = sk.label(ent, "ja")
    alias_limit = 4 if is_person else 1
    names = ([ja] if ja else []) + [a["value"] for a in (ent.get("aliases") or {}).get("ja", [])][:alias_limit]
    for n in names:
        n = re.sub(r"\s*[（(].*?[）)]\s*", "", n or "").strip()
        if len(n) >= 2 and n not in out:
            out.append(n)
        if is_person and n and "・" in n:
            last = n.split("・")[-1]
            if len(last) >= 2 and last not in out:
                out.append(last)
    return out


def main():
    live = "--live" in sys.argv
    cfg = config_utils.load_config()
    client = geo_db_client.SupabaseClient(cfg)
    wd = sk.Wikidata(config_utils.make_proxies(cfg), cfg.get("ssl", {}).get("verify", True))
    results = {"mode": "live" if live else "dry_run", "started_at": datetime.now(timezone.utc).isoformat(),
               "enriched": [], "persons": []}

    ents = []
    off = 0
    while True:
        got = client.select("geo_entities", {"select": "entity_id,entity_type,canonical_name,name_en,name_ja,abbreviations,external_ids",
                                             "limit": "500", "offset": str(off)})
        ents += got
        if len(got) < 500:
            break
        off += 500
    aliases = {}
    off = 0
    while True:
        got = client.select("geo_entity_aliases", {"select": "entity_id,alias_text", "limit": "1000", "offset": str(off)})
        for a in got:
            aliases.setdefault(a["entity_id"], set()).add(a["alias_text"])
        if len(got) < 1000:
            break
        off += 1000
    by_name = {e["canonical_name"].casefold(): e for e in ents}

    # --- 1. 既存エンティティの補強 ---
    for i, e in enumerate(ents, 1):
        have = aliases.get(e["entity_id"], set())
        wanted_short = SHORT_FORMS.get(e["canonical_name"], [])
        need_ja = not e.get("name_ja")
        missing_short = [s for s in wanted_short if s not in have and s != e.get("name_ja")]
        if not need_ja and not missing_short and any(re.search(r"[ぁ-ヶ一-龥]", a) for a in have):
            continue
        item = {"entity": e["canonical_name"], "type": e["entity_type"]}
        print(f"  enrich {i}/{len(ents)}: {e['canonical_name']}", flush=True)
        add = []
        qid = (e.get("external_ids") or {}).get("wikidata")
        ent_wd = None
        try:
            if not qid:
                base = re.sub(r"\s*\(.*?\)\s*", "", e["canonical_name"]).strip()
                res = sk.resolve_seed(wd, e["canonical_name"], base)
                if res["ok"]:
                    qid, ent_wd = res["qid"], res["ent"]
            elif need_ja or not any(re.search(r"[ぁ-ヶ一-龥]", a) for a in have):
                ent_wd = wd.entity(qid)
        except Exception as ex:  # noqa: BLE001
            item["wikidata_error"] = f"{type(ex).__name__}: {str(ex)[:60]}"
        patch = {}
        if ent_wd is not None:
            ja_names = derive_ja_aliases(ent_wd, e["entity_type"] == "person")
            if need_ja and sk.label(ent_wd, "ja"):
                patch["name_ja"] = sk.label(ent_wd, "ja")
            if not e.get("name_en") and sk.label(ent_wd, "en"):
                patch["name_en"] = sk.label(ent_wd, "en")
            if qid and not (e.get("external_ids") or {}).get("wikidata"):
                patch["external_ids"] = dict(e.get("external_ids") or {}, wikidata=qid, source="foundation_entity_enrich")
            add += [(n, "local_language", "ja") for n in ja_names]
        add += [(s, "abbreviation" if re.fullmatch(r"[A-Z]{2,6}", s) else "local_language", None if re.fullmatch(r"[A-Z]{2,6}", s) else "ja")
                for s in wanted_short if len(s) >= 2]
        new_alias = []
        seen = set(have)
        for text, atype, lang in add:
            if text and text not in seen and text != patch.get("name_ja", e.get("name_ja")):
                seen.add(text)
                new_alias.append({"entity_id": e["entity_id"], "alias_text": text, "alias_type": atype, "language": lang})
        item.update(patch=list(patch), new_aliases=[a["alias_text"] for a in new_alias], wikidata=qid)
        if live:
            if patch:
                client.update("geo_entities", {"entity_id": f"eq.{e['entity_id']}"}, patch)
            if new_alias:
                client.insert("geo_entity_aliases", new_alias)
        results["enriched"].append(item)

    # --- 2. 人物の追加 ---
    for canon, wd_label in PERSONS:
        item = {"person": canon}
        if canon.casefold() in by_name:
            item["status"] = "exists"
            results["persons"].append(item)
            continue
        print(f"  person: {canon}", flush=True)
        try:
            res = sk.resolve_seed(wd, canon, wd_label)
            if not res["ok"]:
                item.update(status="skipped_unverified", candidates=res.get("candidates"))
                results["persons"].append(item)
                continue
            ent, qid = res["ent"], res["qid"]
            is_human = any(isinstance(v, dict) and v.get("id") == "Q5" for v in sk.claim_values(ent, "P31"))
            if not is_human:
                item.update(status="skipped_not_human", wikidata=qid)
                results["persons"].append(item)
                continue
            ja_names = derive_ja_aliases(ent, True)
            country_codes = []
            record = {"entity_type": "person", "canonical_name": canon, "name_en": sk.label(ent, "en") or wd_label,
                      "name_ja": sk.label(ent, "ja"), "abbreviations": [], "country_code": None,
                      "external_ids": {"wikidata": qid, "source": "foundation_entity_enrich"}, "is_demo": False}
            item.update(status="to_create" if not live else "created", wikidata=qid, name_ja=record["name_ja"], ja_aliases=ja_names)
            if live:
                created = client.insert("geo_entities", [record])[0]
                item["entity_id"] = created["entity_id"]
                rows, seen = [], set()
                for n in ja_names:
                    if n and n != record["name_ja"] and n not in seen:
                        seen.add(n)
                        rows.append({"entity_id": created["entity_id"], "alias_text": n, "alias_type": "local_language", "language": "ja"})
                for a in (ent.get("aliases") or {}).get("en", [])[:3]:
                    v = a.get("value")
                    if v and v.casefold() != canon.casefold() and v not in seen:
                        seen.add(v)
                        rows.append({"entity_id": created["entity_id"], "alias_text": v, "alias_type": "transliteration", "language": "en"})
                if rows:
                    client.insert("geo_entity_aliases", rows)
        except Exception as ex:  # noqa: BLE001
            item.update(status="error", reason=f"{type(ex).__name__}: {str(ex)[:80]}")
        results["persons"].append(item)

    results["finished_at"] = datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    pc = {}
    for p in results["persons"]:
        pc[p["status"]] = pc.get(p["status"], 0) + 1
    print("enriched entities:", len(results["enriched"]), "| persons:", json.dumps(pc, ensure_ascii=False), "\nresults:", OUT, flush=True)


if __name__ == "__main__":
    main()
