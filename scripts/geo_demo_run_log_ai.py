"""デモ用: 【デモ用ダミー】の面談ログ（AI整理が未実行のもの）に、AI整理（実LLM）を実行して保存する。

- 対象は、メモ冒頭に「【デモ用ダミー】」があり、ai_status が not_run のログだけ。
- 既定は dry-run（対象の件数を表示するだけ）。--apply を付けたときだけ、LLMを呼んで保存する（1ログ=1回前後）。
使い方: python scripts/geo_demo_run_log_ai.py --user-id auto_xxxx [--apply] [--workers 3]
"""
import argparse
import io
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import geo_external_network_service as svc  # noqa: E402
from ai_client import make_openai_client  # noqa: E402
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402
from geo_ios_identity import IosIdentity  # noqa: E402

TAG = "【デモ用ダミー】"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-id", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()
    cfg = load_config()
    client = SupabaseClient(cfg)
    ident = IosIdentity(identity_id=args.user_id, assigned_role="analyst", authenticated=True)
    rows = client.select("geo_external_contact_logs", {"select": "log_id,memo,ai_status,contact_date", "status": "eq.active",
                                                        "order": "contact_date.asc", "limit": "1000"})
    targets = [r for r in rows if (r.get("memo") or "").startswith(TAG) and r.get("ai_status") == "not_run"]
    print(f"対象: {len(targets)}件（デモ用ダミーで、AI整理が未実行のログ）")
    if not args.apply:
        return 0
    azure, model = make_openai_client(cfg)
    if not azure:
        print("AIのAPIキーが未設定のため実行できません")
        return 1

    def one(r):
        t0 = time.monotonic()
        try:
            svc.run_ai_summary(client, azure, model, ident, r["log_id"])
            return (r["log_id"], "ok", round(time.monotonic() - t0, 1))
        except Exception as exc:  # noqa: BLE001
            return (r["log_id"], f"失敗: {type(exc).__name__} {str(exc)[:80]}", round(time.monotonic() - t0, 1))

    ok = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for log_id, status, sec in pool.map(one, targets):
            print(f"  {log_id[:8]} {status} {sec}s")
            ok += status == "ok"
    print(f"完了: 成功{ok}/{len(targets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
