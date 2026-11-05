"""SSO導入の移行用: 既存のID（auto_xxxx / pilot_xxxx）に、SSOのメールアドレスを紐づける。ロールの変更もできる。

- 初回のSSOログインで、そのメールのユーザーが、このIDに紐づく（それまでの専門家相談・コンタクト・面談ログが、そのまま引き継がれる）。
- 既定は dry-run（書き込みなし）。--apply を付けたときだけ更新する。資格情報（credential）は、表示しない。
- 前提: sql/2026-10-23_geo_ios_identities_sso.sql が、DBに適用されていること。

使い方:
  python scripts/geo_sso_link_identity.py --list
  python scripts/geo_sso_link_identity.py --identity-id auto_deac2a2d86f9 --email taro.yamada@example.com [--role approver] [--apply]
"""
import argparse
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import geo_role_guard  # noqa: E402
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

COLUMNS = "identity_id,role,display_name,email,sso_subject,is_active,created_by"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="IDの一覧（資格情報は出さない）を表示する")
    ap.add_argument("--identity-id")
    ap.add_argument("--email")
    ap.add_argument("--role", help=f"ロールも変える場合（{', '.join(geo_role_guard.ROLES)}）")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    client = SupabaseClient(load_config())
    try:
        rows = client.select("geo_ios_identities", {"select": COLUMNS, "order": "created_at.desc", "limit": "500"})
    except Exception:  # noqa: BLE001
        print("読めません。sql/2026-10-23_geo_ios_identities_sso.sql が、未適用の可能性があります。")
        return 1
    if args.list or not args.identity_id:
        for r in rows:
            link = "SSO連携済み" if r.get("sso_subject") else ("メール登録済み" if r.get("email") else "未連携")
            print(f"  {r['identity_id']:<22} {r['role']:<18} {link:<12} {r.get('email') or '-':<34} {r.get('display_name') or '-'}"
                  f"{'' if r.get('is_active') else '  (無効)'}")
        print(f"計{len(rows)}件")
        return 0
    if not args.email or "@" not in args.email:
        print("--email に、SSOのメールアドレス（UPN）を指定してください")
        return 1
    if args.role and args.role not in geo_role_guard.ROLES:
        print(f"--role が不正です: {args.role}")
        return 1
    target = next((r for r in rows if r["identity_id"] == args.identity_id), None)
    if not target:
        print(f"IDが見つかりません: {args.identity_id}")
        return 1
    clash = next((r for r in rows if (r.get("email") or "").lower() == args.email.lower() and r["identity_id"] != args.identity_id), None)
    if clash:
        print(f"このメールは、すでに別のID（{clash['identity_id']}）に登録されています。")
        return 1
    if target.get("sso_subject"):
        print("このIDは、すでにSSOアカウントと連携済みです。メールの変更は、DBで直接行ってください。")
        return 1
    patch = {"email": args.email}
    if args.role:
        patch["role"] = args.role
    print(f"{args.identity_id}: email {target.get('email') or '-'} -> {args.email}"
          + (f" / role {target['role']} -> {args.role}" if args.role else "") + f"  [{'更新' if args.apply else 'dry-run（書き込みなし）'}]")
    if args.apply:
        client.update("geo_ios_identities", {"identity_id": f"eq.{args.identity_id}"}, patch)
        print("更新しました。次のSSOログインで、このメールのユーザーが、このIDに紐づきます。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
