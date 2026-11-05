# -*- coding: utf-8 -*-
"""Foundation 段階3: 見送りになったナレッジ候補を、AIが「出典で確認できる内容だけ」に直し、再検証して、通ったものだけAI暫定承認する。

人のチェックの代わりに、AI内で次を重ねる（既存の geo_knowledge_ai_review_service の検証をそのまま使う）:
  1. 校正: AI評価が見つけた問題（出典で確認できない記述・過度な一般化・事実の誤り・断定的なラベル）を、
     評価が確認した事実（verified_points）と出典だけを根拠に直す。新しい事実は一切足さない。
     直せない記述は、断定を弱める（「〜とされる」「〜と報じられた」）か、その値を削る（キーは残す）。
  2. 構造検査: 校正後の内容は、元と同じキー構造（型・キー集合）を保つこと。極端に短くなった/長くなった場合は不採用。
  3. 再検証: 校正後の内容を、独立に複数回（既存どおり2〜3回）Web出典で評価し直す。
  4. 承認: 承認条件を満たし、出自の検証にも通ったものだけ、内容を更新（version+1、元の内容は監査ログに保存）して
     AI暫定承認（approved_by="ai:<モデル名>"、人の承認とは区別される）にする。通らなければ内容は一切変えず候補のまま。
校正は最大2ラウンド。既定は dry-run（DBを書き換えない）。--live で内容更新と承認を行う。

使い方:
    python scripts/foundation_knowledge_revise.py --from evaluation/KNOWLEDGE_AI_REVIEW_FOUNDATION_A.json [--live] [--limit N]
結果: evaluation/FOUNDATION_KNOWLEDGE_REVISION_RESULTS.json
"""
import argparse
import copy
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_client import make_openai_client  # noqa: E402
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402
import geo_audit  # noqa: E402
import geo_knowledge_ai_review_service as svc  # noqa: E402
import geo_knowledge_service as ks  # noqa: E402
import geo_llm_common  # noqa: E402
import geo_perplexity_search as pplx  # noqa: E402

OUT = ROOT / "evaluation" / "FOUNDATION_KNOWLEDGE_REVISION_RESULTS.json"
MAX_ROUNDS = 2
WORKERS = 3

REVISE_SCHEMA = {"type": "object", "properties": {
    "revised_json": {"type": "string"}, "changes": {"type": "array", "items": {"type": "string"}}},
    "required": ["revised_json", "changes"], "additionalProperties": False}

SYSTEM = """あなたは地政学ナレッジの校正担当です。ナレッジ項目の内容(JSON)を、「出典で確認できる内容だけ」に直します。

厳守:
1. 新しい事実・日付・数値・固有名詞・発言を一切足さない。使ってよい事実は「確認できた点(verified_points)」に書かれたものだけ。
2. 評価が指摘した問題(unsupported=出典で確認できない / overgeneralization=過度な一般化 / factual_error=事実の誤り /
   bias=断定的なラベル / outdated=古い)に該当する記述だけを直す。問題の無い記述は一字も変えない。
3. 直し方: (a) 事実の誤りは、確認できた点にある正しい内容に置き換える。置き換える根拠が無ければその記述を削る。
   (b) 出典で確認できない具体的な日付・引用・内訳は、確認できる粒度（年・月など）へ弱めるか削る。
   (c) 過度な一般化・断定は「〜とされる」「〜と報じられた」「主要な報道では〜」へ弱める。
   (d) 内部の管理用の記述（エピソード番号・内部ID・システム用語）は削る。
4. JSONの構造は変えない。キーは全て残し、型（文字列/配列/オブジェクト/null）も保つ。値を削る場合は文字列なら空文字、配列なら要素を減らす。
5. 出力は revised_json（直した後の内容全体を、JSON文字列として）と changes（何をどう直したかの短い箇条書き、各1文）。"""


def same_shape(a, b):
    """校正後の構造が元と同じか（キー集合・型。配列は要素数が減るのは可、型は同じ）。"""
    if isinstance(a, dict):
        return isinstance(b, dict) and set(a) == set(b) and all(same_shape(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return isinstance(b, list)
    if a is None:
        return b is None or isinstance(b, (str, list, dict, int, float, bool))
    return type(a) is type(b) or (isinstance(a, (int, float)) and isinstance(b, (int, float)))


def text_len(o):
    return len(json.dumps(o, ensure_ascii=False))


def revise_once(azure, model, item, ev, sources):
    problems = [f"[{p['kind']}] {p['text']}" for p in (ev.get("problems") or [])]
    fixes = list(ev.get("suggested_fixes") or [])
    verified = list(ev.get("verified_points") or [])
    src = "\n".join(f"[{i}] {s.get('title') or ''} {s.get('url')}" for i, s in enumerate(sources, 1))
    user = (f"# ナレッジ項目\n種類: {item.get('knowledge_type')}\nタイトル: {item.get('title')}\n"
            f"内容(JSON):\n{json.dumps(item.get('structured_content'), ensure_ascii=False)}\n\n"
            f"# 評価が指摘した問題\n" + ("\n".join(f"- {p}" for p in problems) or "（なし）") +
            f"\n\n# 直し方の提案\n" + ("\n".join(f"- {f}" for f in fixes) or "（なし）") +
            f"\n\n# 確認できた点(verified_points。これ以外の事実は足さない)\n" + ("\n".join(f"- {v}" for v in verified) or "（なし）") +
            f"\n\n# 出典\n{src}")
    res = geo_llm_common.call_llm_structured(azure, model, SYSTEM, user, REVISE_SCHEMA, "foundation_knowledge_revise")["data"]
    revised = json.loads(res["revised_json"])
    return revised, [c[:200] for c in res["changes"]]


def process(item_row, prior, args, ctx):
    client, azure, model, settings = ctx
    kid = item_row["knowledge_item_id"]
    out = {"knowledge_item_id": kid, "title": item_row.get("title"), "type": item_row.get("knowledge_type"), "rounds": []}
    if item_row.get("status") not in ("candidate", "in_review"):
        out["final"] = f"skipped_status:{item_row.get('status')}"
        return out
    current = copy.deepcopy(item_row)
    ev, sources = prior["evaluation"], prior["sources"]
    original = item_row.get("structured_content")
    for rnd in range(1, MAX_ROUNDS + 1):
        rinfo = {"round": rnd}
        try:
            revised, changes = revise_once(azure, model, current, ev, sources)
        except Exception as e:  # noqa: BLE001
            rinfo["error"] = f"revise:{type(e).__name__}: {str(e)[:80]}"
            out["rounds"].append(rinfo)
            break
        rinfo["changes"] = changes
        base = current.get("structured_content")
        if not same_shape(base, revised):
            rinfo["rejected"] = "structure_changed"
            out["rounds"].append(rinfo)
            break
        ratio = text_len(revised) / max(1, text_len(base))
        rinfo["size_ratio"] = round(ratio, 2)
        if ratio < 0.5 or ratio > 1.15:
            rinfo["rejected"] = f"size_out_of_range:{ratio:.2f}"
            out["rounds"].append(rinfo)
            break
        trial = dict(current, structured_content=revised)
        try:
            result = svc.evaluate_item(azure, model, settings, trial)
        except Exception as e:  # noqa: BLE001
            rinfo["error"] = f"evaluate:{type(e).__name__}: {str(e)[:80]}"
            out["rounds"].append(rinfo)
            break
        rinfo["eligible"] = bool(result.get("eligible"))
        rinfo["reasons"] = (result.get("reasons") or [])[:3]
        rinfo["runs"] = [{"eligible": r["eligible"], "facts": f"{r['facts_verified']}/{r['facts_total']}"} for r in result.get("runs", [])]
        out["rounds"].append(rinfo)
        if result.get("eligible"):
            if args.live:
                client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{kid}"},
                              {"structured_content": revised, "version": (current.get("version") or 1) + 1})
                geo_audit.log_audit(client, actor=f"{svc.AI_PREFIX}{model}", action="knowledge_item_ai_revision",
                                    target_table="geo_knowledge_items", target_id=kid,
                                    detail={"rounds": rnd, "changes": changes, "original_structured_content": original})
                applied = svc.apply_ai_provisional_approval(client, result, model=model)
                out["final"] = "approved" if applied["applied"] else f"revised_but_not_approved:{applied['reason']}"
            else:
                out["final"] = "would_approve"
            return out
        # 次ラウンド: 今回の評価の問題点を使ってさらに直す
        current = trial
        ev, sources = result["evaluation"], result["sources"]
    out["final"] = "kept_candidate"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", type=Path, required=True)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--types", nargs="*", default=None)
    args = ap.parse_args()
    cfg = load_config()
    client = SupabaseClient(cfg)
    azure, model = make_openai_client(cfg)
    settings = pplx.get_settings()
    if not azure or not settings:
        sys.exit("Azure/Perplexityの設定が無いため実行できません")
    data = json.loads(args.src.read_text(encoding="utf-8"))
    targets = [r for r in data["results"] if "error" not in r and not r.get("eligible")
               and (r.get("evaluation") or {}).get("problems")]
    if args.types:
        targets = [r for r in targets if r["knowledge_type"] in args.types]
    if args.limit:
        targets = targets[:args.limit]
    print(f"mode={'LIVE' if args.live else 'DRY-RUN'} | 校正対象 {len(targets)}件", flush=True)
    ctx = (client, azure, model, settings)

    def run(prior):
        rows = client.select("geo_knowledge_items", {"select": "*", "knowledge_item_id": f"eq.{prior['knowledge_item_id']}", "limit": "1"})
        if not rows:
            return {"knowledge_item_id": prior["knowledge_item_id"], "final": "not_found"}
        try:
            r = process(rows[0], prior, args, ctx)
        except Exception as e:  # noqa: BLE001
            r = {"knowledge_item_id": prior["knowledge_item_id"], "title": prior.get("title"), "final": f"error:{type(e).__name__}: {str(e)[:80]}"}
        print(f"  {r.get('final')}: {(r.get('title') or '')[:40]}", flush=True)
        return r

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        results = list(ex.map(run, targets))
    summary = {}
    for r in results:
        summary[r["final"].split(":")[0]] = summary.get(r["final"].split(":")[0], 0) + 1
    OUT.write_text(json.dumps({"mode": "live" if args.live else "dry_run", "model": model,
                               "finished_at": datetime.now(timezone.utc).isoformat(), "summary": summary,
                               "results": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("summary:", json.dumps(summary, ensure_ascii=False), "\nresults:", OUT, flush=True)


if __name__ == "__main__":
    main()
