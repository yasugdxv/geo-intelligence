"""デモ用コピーの読み筋タイトルを、新しい共通関数（geo_ios_title_utils.short_title）で作り直す。

- 対象は、指定した分析セッション（既定: デモ用コピー2件）の geo_ios_scenario_candidates.title の列だけ。
- 既定は dry-run（書き込みなし）。--apply を付けたときだけ UPDATE する。元の検証セッションは対象外。
"""
import argparse
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import geo_ios_title_utils as title_utils  # noqa: E402
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

DEMO_SESSIONS = ["ff22c88e-2be3-49cd-84bf-dc9b9b8d8ea4", "2717a2e4-968c-4704-9740-0e18db0b4c9c"]


def _description(content: dict) -> str:
    raw = (content or {}).get("raw") or {}
    for v in (raw.get("description"), (content or {}).get("description"), raw.get("reading"), raw.get("title")):
        if isinstance(v, str) and v.strip():
            return v
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", action="append")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    client = SupabaseClient(load_config())
    for sid in args.session or DEMO_SESSIONS:
        rows = client.select("geo_ios_scenario_candidates", {
            "select": "*", "analysis_session_id": f"eq.{sid}", "order": "rank.asc", "limit": "50"})
        print(f"== {sid} ({len(rows)}件)")
        for r in rows:
            desc = _description(r.get("content"))
            new = title_utils.short_title(desc) if desc else None
            old = r.get("title")
            if not title_utils.looks_broken(old):
                print(f"  rank{r.get('rank')}: 良好のため変更なし {old!r}")
                continue
            if not new or new == old:
                print(f"  rank{r.get('rank')}: 変更なし {old!r}")
                continue
            print(f"  rank{r.get('rank')}: {old!r}\n      -> {new!r}")
            if args.apply:
                client.update("geo_ios_scenario_candidates",
                              {"scenario_candidate_id": f"eq.{r['scenario_candidate_id']}"}, {"title": new})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
