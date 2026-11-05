"""ナレッジ候補のAI評価と、AI暫定承認。

既定（評価のみ）: 候補を、Web調査（Perplexity・出典つき）と専門家としての評価で判定し、結果をファイルに出す。
  DBへは何も書かない。出力: evaluation/KNOWLEDGE_AI_REVIEW_DRAFT.json / .md
--apply <json>: 評価ファイルのうち、承認条件を満たしたものだけを「AI暫定承認」にする（approved_by="ai:<モデル名>"）。
  満たさないものは候補のまま、AIの所見だけを監査ログに残す。出自の検証は、人の承認と同じものを通す。

使い方:
  python scripts/geo_knowledge_ai_review.py --limit 10                    # 先に10件を評価（書き込みなし）
  python scripts/geo_knowledge_ai_review.py --types actor_profile --limit 20
  python scripts/geo_knowledge_ai_review.py --apply evaluation/KNOWLEDGE_AI_REVIEW_DRAFT.json   # 反映
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_client import make_openai_client            # noqa: E402
from config_utils import load_config                 # noqa: E402
from geo_db_client import SupabaseClient              # noqa: E402
import geo_knowledge_ai_review_service as svc        # noqa: E402
import geo_perplexity_search as pplx                  # noqa: E402

OUT_JSON = ROOT / "evaluation" / "KNOWLEDGE_AI_REVIEW_DRAFT.json"
OUT_MD = ROOT / "evaluation" / "KNOWLEDGE_AI_REVIEW_DRAFT.md"
WORKERS = 3


def write_outputs(results: list, model: str) -> None:
    OUT_JSON.write_text(json.dumps({"model": model, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = [r for r in results if r.get("eligible")]
    lines = [f"# ナレッジ候補のAI評価（下書き。DBには書き込んでいない）\n",
             f"- 評価モデル: {model} / 評価 {len(results)}件 / **承認条件を満たす {len(ok)}件**",
             "- 反映するまで、状態は変わりません。内容に疑問があれば、そのソースの `eligible` を false に直してから反映してください。\n"]
    for r in results:
        if "error" in r:
            lines += [f"## ⚠ {r['title']}\n- 評価に失敗: {r['error']}\n"]
            continue
        ev = r["evaluation"]
        mark = "✅ 承認条件を満たす" if r["eligible"] else "⏸ 候補のまま（人の確認・修正が必要）"
        lines += [f"## {r['title']}（{r['knowledge_type']}）\n`{r['knowledge_item_id']}`　時点: {r.get('as_of_date') or '不明'}"]
        if r.get("mode") == "interpretive":
            facts = ev["facts"]
            ok = sum(1 for f in facts if f["status"] == "verified")
            lines.append(f"- **{mark}**　事実 {ok}/{len(facts)}件を出典で確認 / 解釈: {ev['interpretation']['assessment']} / 確信度: {ev['confidence']}")
        else:
            lines.append(f"- **{mark}**　AI判定: {ev['verdict']} / 根拠: {ev['evidence_adequacy']} / 確信度: {ev['confidence']}")
        if r.get("runs"):
            lines.append("- 各回の評価: " + " / ".join(
                f"{i}回目 {'○' if x['eligible'] else '×'}"
                + (f"（事実 {x['facts_verified']}/{x['facts_total']}件）" if x.get("facts_total") else "")
                for i, x in enumerate(r["runs"], 1)))
        if r["reasons"]:
            lines.append("- 条件を満たさない理由: " + "；".join(r["reasons"]))
        lines.append(f"- 理由: {ev['rationale']}")
        if r.get("mode") == "interpretive":
            icon = {"verified": "✔確認", "contradicted": "✖矛盾", "unverified": "？未確認"}
            lines.append("- 事実の部分:\n" + "\n".join(f"  - {icon[f['status']]} {f['claim']}" for f in ev["facts"][:12]))
            lines.append(f"- 分析・解釈の部分（**AIの見立てとして扱う**）: {ev['interpretation']['summary']}")
            if ev["interpretation"]["concerns"]:
                lines.append("  - 懸念: " + " / ".join(ev["interpretation"]["concerns"][:4]))
        elif ev["verified_points"]:
            lines.append("- 確認できた点:\n" + "\n".join(f"  - {p}" for p in ev["verified_points"][:6]))
        if ev.get("problems"):
            lines.append("- 問題点:\n" + "\n".join(f"  - [{p['kind']}] {p['text']}" for p in ev["problems"]))
        if ev["suggested_fixes"]:
            lines.append("- 直し方:\n" + "\n".join(f"  - {p}" for p in ev["suggested_fixes"][:5]))
        lines.append("- 出典:\n" + "\n".join(f"  {i}. {s['title'] or ''} {s['url']} ({s['date'] or '日付不明'})"
                                           for i, s in enumerate(r["sources"], 1)) + "\n")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--types", nargs="*", default=None, help="評価する種類（例: actor_profile）")
    ap.add_argument("--titles", nargs="*", default=None, help="評価する項目のタイトル（部分一致。例: Putin Trump）")
    ap.add_argument("--apply", type=Path, default=None, help="評価ファイルを反映する（AI暫定承認）")
    args = ap.parse_args()
    cfg = load_config()
    client = SupabaseClient(cfg)
    azure_client, model = make_openai_client(cfg)
    if azure_client is None:
        sys.exit("AI分析エンジンが設定されていません")

    if args.apply:
        data = json.loads(args.apply.read_text(encoding="utf-8"))
        applied = held = 0
        for r in data["results"]:
            if "error" in r:
                continue
            out = svc.apply_ai_provisional_approval(client, r, model=data["model"])
            if out["applied"]:
                applied += 1
                print(f"  AI暫定承認: {r['title']}")
            else:
                held += 1
                svc.record_hold(client, r, model=data["model"])
                print(f"  見送り: {r['title']} — {out['reason'] if r.get('eligible') else '; '.join(r.get('reasons') or [])[:70]}")
        print(f"反映: AI暫定承認 {applied}件 / 候補のまま {held}件")
        return

    settings = pplx.get_settings()
    if settings is None:
        sys.exit("Perplexityのキーが未設定です（Web調査ができないため、評価できません）")
    items = svc.candidate_items(client, types=args.types, limit=10_000 if args.titles else args.limit)
    if args.titles:
        items = [i for i in items if any(t.lower() in (i.get('title') or '').lower() for t in args.titles)]
    print(f"評価対象 {len(items)}件（DBには書き込みません）")

    def run(item):
        try:
            comments = svc.list_expert_comments(client, item["knowledge_item_id"])   # 専門家のコメントも材料にする
            return svc.evaluate_item(azure_client, model, settings, item, expert_comments=comments)
        except Exception as exc:  # noqa: BLE001 — 1件の失敗で全体を止めない
            return {"knowledge_item_id": item["knowledge_item_id"], "title": item.get("title"),
                    "knowledge_type": item.get("knowledge_type"), "error": str(exc)[:120], "eligible": False}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        results = list(ex.map(run, items))
    write_outputs(results, model)
    ok = sum(1 for r in results if r.get("eligible"))
    print(f"完了: 承認条件を満たす {ok}/{len(results)}件 → {OUT_MD}")


if __name__ == "__main__":
    main()
