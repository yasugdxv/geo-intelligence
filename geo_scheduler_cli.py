"""Phase D: Scheduler CLI（OSのtimer/cron/Task Schedulerから呼ぶ入口）。

使い方（プロジェクト直下で実行）:
    python geo_scheduler_cli.py tick                      # 今やるべきJobを判定して実行（15分ごとに呼ぶ）
    python geo_scheduler_cli.py tick --only COLLECTION_OFAC   # 専用枠のJob（OFAC）だけ（別timer用）
    python geo_scheduler_cli.py tick --no-heartbeat       # 手動・検証用（稼働印を更新しない）
    python geo_scheduler_cli.py plan                      # Dry Run（DBを一切変更しない）
    python geo_scheduler_cli.py status                    # Scheduler/Jobの状態を表示
    python geo_scheduler_cli.py run COLLECTION_GDELT_DOC  # 手動実行（周期・backoffを無視して今すぐ）
    python geo_scheduler_cli.py recover-stale [--dry-run] # 取り残されたrunningをfailedへ補正

終了コード: 0=正常 / 1=失敗したJobがある / 2=使い方・多重起動
LLMは使わない。Human Gate（Episode ClusterのAccept）とTheme Syncは実行しない。
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

LOCK_DIR = Path(os.environ.get("GEO_SCHEDULER_LOCK_DIR", Path(__file__).parent / "logs"))
TICK_LOCK_STALE_SECONDS = 3 * 3600      # tickが3時間以上続いたら取り残された鍵とみなす


class TickLock:
    """tickの多重起動防止（OSレベル。Jobごとの二重実行防止はDBのrunning行が担当）。
    O_EXCLで作るロックファイル。プロセスが落ちて残った鍵は、古ければ自動で無効とみなす。"""

    def __init__(self, name: str):
        self.path = LOCK_DIR / f"scheduler_{name}.lock"
        self.fd = None

    def acquire(self) -> bool:
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, f"pid={os.getpid()} at={time.time()}".encode())
                return True
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > TICK_LOCK_STALE_SECONDS:
                        self.path.unlink()          # 古い鍵（クラッシュの残り）だけ片付ける
                        continue
                except OSError:
                    pass
                return False
        return False

    def release(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
            try:
                self.path.unlink()
            except OSError:
                pass


def _print_jobs(jobs: list) -> None:
    for j in jobs:
        extra = f" | last_success={j['last_success_at']}" if j.get("last_success_at") else ""
        print(f"  {j['job_id']:<26} {j['state']:<14} {j.get('action', ''):<12} {j['reason']}{extra}")


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):      # Windowsのcp932でも日本語が文字化けしないように
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="地政学インテリジェンス Scheduler")
    sub = parser.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tick")
    t.add_argument("--only", nargs="*", default=None, help="指定Jobだけを対象にする（専用枠のJobはここで指定）")
    t.add_argument("--dry-run", action="store_true")
    t.add_argument("--is-demo", action="store_true")
    t.add_argument("--no-heartbeat", action="store_true",
                   help="手動・検証用。定期実行の稼働印(heartbeat)を更新しない（OSのtimer/cronからは付けない）")
    sub.add_parser("plan").add_argument("--only", nargs="*", default=None)
    s = sub.add_parser("status")
    s.add_argument("--json", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("job_id")
    r.add_argument("--force", action="store_true", help="実行中ロック・依存を無視（障害対応用。通常は不要）")
    rs = sub.add_parser("recover-stale")
    rs.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    from config_utils import load_config
    from geo_db_client import SupabaseClient
    import geo_scheduler_service as svc
    import geo_scheduler_tick as tick

    client = SupabaseClient(load_config())
    registry = svc.load_job_registry()
    now = tick._now()

    if args.cmd == "plan" or (args.cmd == "tick" and args.dry_run):
        res = tick.run_tick(client, registry=registry, now=now, only=getattr(args, "only", None), dry_run=True,
                            include_dedicated=True)
        print(f"[PLAN / Dry Run: DBは変更しません] {res['tick_at']}")
        print("補正対象のstale running:", json.dumps(res["would_recover_stale"], ensure_ascii=False))
        _print_jobs(res["jobs"])
        return 0

    if args.cmd == "tick":
        lock = TickLock("tick_" + ("-".join(args.only) if args.only else "default"))
        if not lock.acquire():
            if not args.no_heartbeat:
                tick.touch_heartbeat(client, now=now, info={"phase": "skipped_previous_tick_running"})
            print("前回のtickがまだ実行中のため、今回はskipしました（多重起動防止）")
            return 0
        try:
            res = tick.run_tick(client, registry=registry, now=now, only=args.only, is_demo=args.is_demo,
                                heartbeat=not args.no_heartbeat)
        finally:
            lock.release()
        print(f"[TICK] {res['tick_at']} ran={res['jobs_ran']} recovered_stale={len(res['recovered_stale'])}")
        _print_jobs(res["jobs"])
        return 1 if any(j.get("outcome") == "failed" for j in res["jobs"]) else 0

    if args.cmd == "status":
        ov = tick.scheduler_overview(client, registry=registry, now=now)
        if args.json:
            print(json.dumps(ov, ensure_ascii=False, indent=1))
        else:
            print(f"Scheduler: {ov['status']} (last heartbeat: {ov['last_heartbeat_at']}, age={ov['age_minutes']}分)")
            print(ov["note"])
            _print_jobs(ov["jobs"])
            if ov["stale_running"]:
                print("stale running:", json.dumps(ov["stale_running"], ensure_ascii=False))
        return 0

    if args.cmd == "run":
        res = tick.execute_job(client, args.job_id, registry, now=now, trigger="manual",
                               requested_by="cli", force=args.force)
        print(json.dumps({k: v for k, v in res.items() if k != "result"}, ensure_ascii=False, default=str))
        return 0 if res.get("outcome") in ("executed", "skipped", "not_configured") and res.get("scheduler_status") != "failed" else 1

    if args.cmd == "recover-stale":
        rec = tick.recover_stale(client, registry, now=now, dry_run=args.dry_run)
        print(("[DRY RUN] 補正対象: " if args.dry_run else "補正しました: ") + json.dumps(rec, ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
