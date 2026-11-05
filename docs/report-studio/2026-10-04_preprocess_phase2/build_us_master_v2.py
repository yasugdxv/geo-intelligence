"""US Midterm Narrative Master を keyword_matcher_v2 向けに修正する（決定表ベース、自動の一括書き換えではない）。

入力: 修正前のUS master（2026-10-02 run cadc7b32 の signal_narrative_master.csv）
出力: signal_narrative_master_us_v2.csv（修正後）、narrative_master_changes_us.csv（変更前後の一覧と理由）

原則（ユーザー指示）:
  - 単にキーワードを削除してfallbackを増やさない。
  - 旧matcher（部分一致）が実際に拾っていた「正しい語形」だけを、masterへ明示して再現率を保つ。
    旧matcherでも拾えなかった語形（例: `lose`→`losing`）への拡張はしない（Narrativeの範囲を勝手に広げない）。
  - 誤ヒット側（`ICE`→`office`、`rent`→`parent`等）はmatcher v2のexact tokenに任せ、語形は明示する。
  - `govern`のように意味が曖昧な語は、本来のNarrative意味（governing ability / governance criticism）に合わせて置換する。
  - `*`は「語頭一致」の明示指定（`vote*`は`voters`/`voted`に当たり`devoted`に当たらない）。

使い方: python build_us_master_v2.py <旧master.csv> <出力dir> [term_audit.csv]
"""
import csv
import sys
from pathlib import Path

csv.field_size_limit(10**9)

FIELDS = ["include_keywords", "required_context_keywords", "exclude_keywords", "public_voice_cues",
          "cluster_split_terms"]

# term -> (置換後の語リスト, 本来捕まえたかった意味 / 旧matcherの誤ヒット / 置換方針)
REPLACE = {
    # ---- 本物の誤ヒットが大きい語 ----
    "govern": (["governance", "governs", "governed"],
               "governing ability / governance criticism。旧: `Governor`(13,374)・`government`(3,007)に当たりContext `Governor`と同一surfaceで自己発火。"
               "`governing`は既にinclude済み。`government`は候補者の統治能力ではなく政府一般なので含めない"),
    "rent": (["rent", "rents", "rental", "rentals", "renter", "renters"],
             "家賃・住宅費。旧: `current`(2,119)・`different`(1,959)・`parent`に当たる。旧が拾っていた正しい語形のみ明示"),
    "lose": (["lose", "loses"],
             "electability（勝てない/負ける）。旧: `close`(3,155)・`closed`・`disclosed`に当たる。旧でも拾えなかった`losing`は足さない"),
    "beat": (["beat", "beats", "beating", "beaten"],
             "electability（誰かを破る）。旧: `Beatty`(558)・`heartbeat`に当たる"),
    "unite": (["unite", "unites"],
              "party reunification。旧: `united`(3,221、`United States`等)に当たる"),
    "ad": (["ad", "ads"],
           "cluster split term（広告）。旧: `had`(2,027)・`head`・`already`・`administration`に当たり36,667件中31,799件が誤ヒット"),
    "rally": (["rally", "rallying"], "集会。旧: `generally`(418)・`literally`に当たる"),
    # ---- exact tokenにすると落ちる正しい語形を明示（再現率の維持） ----
    "Democrat": (["Democrat*"], "required context。旧が拾っていた`democrats`(5,702)・`democratic`(4,195)を語頭一致で維持"),
    "vote": (["vote*"], "required context。`voters`(5,960)・`voted`(1,958)・`votes`を維持。`devoted`には当たらない"),
    "candidate": (["candidate*"], "required context。`candidates`(4,141)を維持"),
    "election": (["election*"], "required context。`elections`(1,737)を維持"),
    "campaign": (["campaign*"], "required context。`campaigns`/`campaigning`を維持"),
    "trust": (["trust", "trusts", "trusted", "trusting"],
              "required context。旧は`trustintrump47`(ハンドル名、1,218)・`antitrust`・`distrust`にも当たっていた。語形のみ明示"),
    "corrupt": (["corrupt", "corruption", "corrupted", "corrupting"], "腐敗。`corruption`(1,421)を維持"),
    "water": (["water", "waters"], "水問題。旧: `watermark`・`bridgewater`・`underwater`に当たる"),
    "environment": (["environment", "environmental", "environments"], "環境。`environmental`(688)を維持"),
    "independent": (["independent", "independents"], "無党派。`independents`(553)を維持"),
    "crime": (["crime", "crimes"], "犯罪。`crimes`(535)を維持"),
    "interview": (["interview", "interviews", "interviewed", "interviewing"], "インタビュー。語形を維持"),
    "data center": (["data center", "data centers"], "データセンター。`data centers`(981)を維持"),
    "property tax": (["property tax", "property taxes"], "固定資産税。`property taxes`(347)を維持"),
    "swing voter": (["swing voter", "swing voters"], "スイング票。`swing voters`(193)を維持"),
    "border": (["border", "borders"], "国境。`borders`(252)を維持"),
    "honest": (["honest", "honesty", "honestly"], "誠実さ。語形を維持（`dishonest`は別語として拾わない）"),
    "debate": (["debate", "debates"], "討論会。`debates`を維持"),
    "switch": (["switch", "switches", "switched", "switching"], "支持の切り替え。語形を維持"),
    "endorsement": (["endorsement", "endorsements"], "支持表明。語形を維持"),
    "speech": (["speech", "speeches"], "演説。`speeches`を維持"),
    "scandal": (["scandal", "scandals"], "スキャンダル。語形を維持"),
    "education": (["education", "educational"], "教育。語形を維持"),
    "trade": (["trade", "trades"], "貿易。旧: `trademark`・`trader`に当たる"),
    "defection": (["defection", "defections"], "split term。`defections`を維持"),
    "moderate": (["moderate", "moderates"], "穏健派。旧: `moderated`(316)に当たる"),
    "undecided": (["undecided", "undecideds"], "未決定層。語形を維持"),
    "union": (["union", "unions"], "労組。旧: `reunion`に当たる"),
    "division": (["division", "divisions"], "党内分裂。旧: `subdivision`に当たる"),
    "campaign ad": (["campaign ad", "campaign ads"], "選挙広告。語形を維持"),
    "attack ad": (["attack ad", "attack ads"], "攻撃広告。語形を維持"),
    "abortion": (["abortion", "abortions"], "中絶。語形を維持"),
    "experience": (["experience", "experiences", "experienced"], "split term。語形を維持"),
    "competence": (["competence", "incompetence"], "split term。旧が拾っていた`incompetence`(429)を維持"),
    "competent": (["competent", "incompetent"], "cue。旧が拾っていた`incompetent`を維持"),
    "employment": (["employment", "unemployment"], "雇用。旧が拾っていた`unemployment`を維持"),
    # ---- required context（役職名・州名）の語形。context欠落239件の原因（rawで確認）。旧が拾っていた形のみ ----
    "Governor": (["Governor", "Governors"], "required context。`governors`(旧で51件のcontext欠落)を維持。`governorship`等は足さない"),
    "New York": (["New York", "New Yorker", "New Yorkers"], "required context。`New Yorkers`(旧で54件のcontext欠落)を維持"),
    "Pennsylvania": (["Pennsylvania", "Pennsylvanians"], "required context。`Pennsylvanians`(旧で21件のcontext欠落)を維持"),
    "Illinois": (["Illinois", "Illinoisans"], "required context。住民表記を維持"),
    "Arizona": (["Arizona", "Arizonans"], "required context。`Arizonans`(旧で112件が部分一致)を維持"),
    "Nevada": (["Nevada", "Nevadans"], "required context。`Nevadans`を維持"),
    "Michigan": (["Michigan", "Michiganders"], "required context。`Michiganders`を維持"),
    "Wisconsin": (["Wisconsin", "Wisconsinites"], "required context。`Wisconsinites`を維持"),
    "Georgia": (["Georgia", "Georgians"], "required context。`Georgians`を維持（形容詞`Georgian`は足さない）"),
    "Maryland": (["Maryland", "Marylanders"], "required context。`Marylanders`を維持"),
}

# includeにだけ追加する語（replaceではなく補強）。ICEは本来のImmigration and Customs Enforcement。
ADD_TO_FIELD = {
    ("include_keywords", "ICE"): (
        ["ICE raids", "ICE agents", "ICE detention", "immigration enforcement"],
        "ICE(Immigration and Customs Enforcement)。`ICE`はcase-sensitiveなexact tokenで一致するため、小文字表記の"
        "投稿は句（`ICE raids`等、case-insensitive）と`immigration enforcement`で補う"),
}

# 見直したが変更しない語（理由つき）。terms_auditのlegacy_only上位で、v2のexact tokenだけで足りるもの。
REVIEWED_NO_CHANGE = {
    "ICE": "アクロニム。v2ではcase-sensitive exact token（office/justice/police/priceに当たらない）。上記の句を追加",
    "unity": "旧: `community`(3,169)・`opportunity`(2,098)に当たる。v2のexact tokenで解消、語形追加不要",
    "gender": "旧: `transgender`(410)に当たる。意図が不明のため追加しない（必要ならmasterで`transgender`を明示）",
    "labor": "旧: `laboratory`・`collaboration`に当たる。v2で解消",
    "transit": "旧: `transition`(171)に当たる。v2で解消",
    "faith": "旧: `faithful`に当たる。v2で解消",
    "religion": "v2で解消（`religions`は旧では拾えていたが影響小）",
    "Ford": "候補者名。旧: `affordability`(1,900)・`afford`に当たる。v2のexact tokenで解消",
    "Barr": "候補者名。旧: `barrow`・`barred`・`barrett`に当たる。v2で解消",
    "James": "候補者名。旧: ハンドル名の一部に当たる。v2で解消",
    "Cooper": "候補者名。旧: `cooperation`に当たる。v2で解消",
    "leaning": "cue。旧: `cleaning`に当たる。v2で解消",
    "healthcare": "旧: `unitedhealthcare`に当たる。v2で解消",
}


def split_items(value: str) -> list:
    return [p.strip() for p in value.replace("|", ";").split(";") if p.strip()]


def join_like(original: str, items: list) -> str:
    return (";" if (";" in original and "; " not in original) else "; ").join(items)


def main(src: str, out_dir: str, audit_csv: str = ""):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    audit = {}
    if audit_csv and Path(audit_csv).exists():
        for r in csv.DictReader(open(audit_csv, encoding="utf-8-sig")):
            audit[r["term"]] = (r["legacy_hits"], r["v2_hits"])
    rows = list(csv.DictReader(open(src, encoding="utf-8-sig")))
    columns = list(rows[0].keys())
    changes = []
    for row in rows:
        for field in FIELDS:
            original = row[field]
            items = split_items(original)
            if not items:
                continue
            new_items: list = []
            for term in items:
                replaced = [term]
                kind, reason = "no_change", ""
                if term in REPLACE:
                    replaced, reason = REPLACE[term]
                    kind = "replace" if replaced != [term] else "no_change"
                for candidate in replaced:
                    if candidate not in new_items:
                        new_items.append(candidate)
                if (field, term) in ADD_TO_FIELD:
                    extra, why = ADD_TO_FIELD[(field, term)]
                    for candidate in extra:
                        if candidate not in new_items:
                            new_items.append(candidate)
                    changes.append({"signal_id": row["signal_source"], "narrative_id": row["sub_narrative_id"],
                                    "field": field, "change_type": "add", "old_term": term,
                                    "new_terms": "; ".join(extra), "reason": why,
                                    "legacy_hits_sample": audit.get(term, ("", ""))[0],
                                    "v2_hits_sample": audit.get(term, ("", ""))[1]})
                if kind == "replace":
                    changes.append({"signal_id": row["signal_source"], "narrative_id": row["sub_narrative_id"],
                                    "field": field, "change_type": "replace", "old_term": term,
                                    "new_terms": "; ".join(replaced), "reason": reason,
                                    "legacy_hits_sample": audit.get(term, ("", ""))[0],
                                    "v2_hits_sample": audit.get(term, ("", ""))[1]})
            row[field] = join_like(original, new_items)
    seen = set()
    for row in rows:
        for field in FIELDS:
            for term in split_items(row[field]):
                if term in REVIEWED_NO_CHANGE and (term, field) not in seen:
                    seen.add((term, field))
                    changes.append({"signal_id": "(all)", "narrative_id": "(all)", "field": field,
                                    "change_type": "reviewed_no_change", "old_term": term, "new_terms": term,
                                    "reason": REVIEWED_NO_CHANGE[term],
                                    "legacy_hits_sample": audit.get(term, ("", ""))[0],
                                    "v2_hits_sample": audit.get(term, ("", ""))[1]})
    with open(out / "signal_narrative_master_us_v2.csv", "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    change_cols = ["signal_id", "narrative_id", "field", "change_type", "old_term", "new_terms", "reason",
                   "legacy_hits_sample", "v2_hits_sample"]
    with open(out / "narrative_master_changes_us.csv", "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=change_cols)
        writer.writeheader()
        writer.writerows(changes)
    by_type = {}
    for c in changes:
        by_type[c["change_type"]] = by_type.get(c["change_type"], 0) + 1
    print("rows", len(rows), "| changes", by_type, "| distinct replaced terms",
          len({c["old_term"] for c in changes if c["change_type"] == "replace"}))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
