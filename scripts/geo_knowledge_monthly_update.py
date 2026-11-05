"""ナレッジ項目の月次更新: 承認済みの項目について、直近の動きをPerplexityで調べ、「最新動向」の部分へ取り込む。

基本の人物像（歴史・思想・意図・パターン）には触れない。「最新動向」は、日付・出典つきの出来事と、AIの見立て
（現在の姿勢に変化の兆しがあるか）で、項目の内容に recent_developments として足される（AIの月次更新のラベルつき）。
更新のたびに、前の状態を監査ログへ残す。

既定: 調べて下書きに出すだけ（DBへは書かない）。 evaluation/KNOWLEDGE_RECENT_UPDATE_DRAFT.json / .md
--apply <json>: 下書きを反映する。
--apply-now: 調べた結果を、そのまま反映する（月次の定期実行向け。最初は下書き→確認→反映を勧める）。

使い方:
  python scripts/geo_knowledge_monthly_update.py --limit 10                 # 下書き（書き込みなし）
  python scripts/geo_knowledge_monthly_update.py --apply evaluation/KNOWLEDGE_RECENT_UPDATE_DRAFT.json
  python scripts/geo_knowledge_monthly_update.py --all --apply-now          # 月次の定期実行（更新待ちの全件）
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

OUT_JSON = ROOT / "evaluation" / "KNOWLEDGE_RECENT_UPDATE_DRAFT.json"
OUT_MD = ROOT / "evaluation" / "KNOWLEDGE_RECENT_UPDATE_DRAFT.md"
WORKERS = 3


def write_outputs(results: list, model: str) -> None:
    OUT_JSON.write_text(json.dumps({"model": model, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 最新動向の更新（下書き。DBには書き込んでいない）\n",
             "- 基本の人物像（歴史・思想）は変えません。「最新動向」の部分だけが、AIのラベルつきで足されます。\n"]
    icon = {"confirms_profile": "裏付け", "new_information": "新情報", "changes_posture": "姿勢の変化の兆し"}
    for r in results:
        if "error" in r:
            lines += [f"## ⚠ {r['title']}\n- 失敗: {r['error']}\n"]
            continue
        rec = r["recent"]
        lines.append(f"## {r['title']}（{r['knowledge_type']}）　{rec['as_of']}時点")
        if rec["no_notable_change"]:
            lines.append("- 注目すべき動きは見つかりませんでした。")
        for e in rec["items"]:
            lines.append(f"- [{icon.get(e['bearing'], e['bearing'])}] {e['date']}: {e['event']}（出典: {', '.join(e['sources'])}）")
        lines.append(f"- **AIの見立て**: {rec['posture_note']}\n")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--all", action="store_true", help="更新待ちの全件を対象にする")
    ap.add_argument("--types", nargs="*", default=None)
    ap.add_argument("--apply", type=Path, default=None)
    ap.add_argument("--apply-now", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    client = SupabaseClient(cfg)
    azure_client, model = make_openai_client(cfg)
    if azure_client is None:
        sys.exit("AI分析エンジンが設定されていません")

    if args.apply:
        data = json.loads(args.apply.read_text(encoding="utf-8"))
        done = 0
        for r in data["results"]:
            if "error" in r:
                continue
            out = svc.apply_recent_developments(client, r, model=data["model"])
            done += 1 if out["applied"] else 0
            print(f"  {'更新' if out['applied'] else '見送り'}: {r['title']}" + ("" if out["applied"] else f" — {out['reason']}"))
        print(f"反映: {done}件")
        return

    settings = pplx.get_settings()
    if settings is None:
        sys.exit("Perplexityのキーが未設定です")
    items = svc.recent_update_candidates(client, types=args.types, limit=10_000 if args.all else args.limit)
    print(f"対象 {len(items)}件（承認済みで、最新動向が未取り込み/45日超）")

    def run(item):
        try:
            return svc.fetch_recent_developments(azure_client, model, settings, item)
        except Exception as exc:  # noqa: BLE001 — 1件の失敗で全体を止めない
            return {"knowledge_item_id": item["knowledge_item_id"], "title": item.get("title"),
                    "knowledge_type": item.get("knowledge_type"), "error": str(exc)[:120]}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        results = list(ex.map(run, items))
    write_outputs(results, model)
    ok = [r for r in results if "error" not in r]
    print(f"調査完了: {len(ok)}/{len(results)}件 → {OUT_MD}")
    if args.apply_now:
        done = sum(1 for r in ok if svc.apply_recent_developments(client, r, model=model)["applied"])
        print(f"反映: {done}件")


if __name__ == "__main__":
    main()
