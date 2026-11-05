"""デモ用: 既存の検証ケース（TC03/TC05）の分析セッションと読み筋を、指定ユーザーIDの相談案件として実DBへコピーする。

- 追加のみ（INSERT）。元の検証データは変更・削除しない。実LLMは呼ばない。
- 既定は dry-run（書き込みなし）。--apply を付けたときだけ書き込む。
- 同じタイトルの案件が、すでにそのユーザーにある場合は作らない（重複防止）。
使い方: python scripts/geo_demo_copy_consult_sessions.py --user-id 大濱 [--apply]
"""
import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import geo_audit  # noqa: E402
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

# (資料のページ, 元の分析セッションID, デモでの表題)。②→①の順に作ると、一覧（新しい順）で①が上に並ぶ。
CASES = [
    ("資料12ページ 検証例②", "75725cf9-26eb-405f-b579-d94926400c91", "ドイツ政治からロシア戦略を読む"),
    ("資料11ページ 検証例①", "e7aa478f-8b1a-42bb-838e-b100f4866369", "パフラヴィー氏の政治的実効性"),
]
CANDIDATE_COLUMNS = ("title", "content", "rank", "displayed", "generated_at", "model", "prompt_version",
                     "cqas_knowledge_version")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-id", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    client = SupabaseClient(load_config())
    existing = client.select("geo_ios_analysis_sessions", {
        "select": "analysis_session_id,title", "user_id": f"eq.{args.user_id}", "limit": "200"})
    existing_titles = {r["title"] for r in existing}
    report = []
    for page, src_id, title in CASES:
        src = client.select("geo_ios_analysis_sessions", {"select": "*", "analysis_session_id": f"eq.{src_id}", "limit": "1"})
        if not src:
            print(f"[中止] {page}: 元のセッションが見つかりません {src_id}")
            return 1
        cands = client.select("geo_ios_scenario_candidates", {
            "select": "*", "analysis_session_id": f"eq.{src_id}", "order": "rank.asc", "limit": "50"})
        cached = sum(1 for c in cands if (c.get("content") or {}).get("presentation_transform"))
        item = {"page": page, "title": title, "source_session": src_id, "candidates": len(cands),
                "displayed": sum(1 for c in cands if c.get("displayed")), "presentation_cached": cached}
        if title in existing_titles:
            item["action"] = "skip（同じ表題の案件が、すでにあります）"
            report.append(item)
            continue
        if not args.apply:
            item["action"] = "dry-run（書き込みなし）"
            report.append(item)
            continue
        row = {"title": title, "theme": src[0].get("theme"), "user_id": args.user_id, "status": "active",
               "mode": "create", "source_theme_candidate_id": None}
        created = client.insert("geo_ios_analysis_sessions", [row])[0]
        new_id = created["analysis_session_id"]
        rows = [{"analysis_session_id": new_id, "selected": False, **{k: c.get(k) for k in CANDIDATE_COLUMNS}} for c in cands]
        client.insert("geo_ios_scenario_candidates", rows)
        geo_audit.log_audit(client, actor=args.user_id, action="analysis_session_create",
                            target_table="geo_ios_analysis_sessions", target_id=new_id,
                            detail={"title": title, "mode": "create", "demo_copy_of": src_id})
        item["action"] = "作成"
        item["new_session"] = new_id
        report.append(item)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
