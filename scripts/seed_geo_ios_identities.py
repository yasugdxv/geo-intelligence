# -*- coding: utf-8 -*-
"""geo_ios_identities（DB）へ、既存config.jsonのgeo_ios.identities一式を移行し、
追加でダミーのexpert_reviewerアカウントを1件作成する。

前提: sql/2026-09-24d_geo_ios_identities.sql をSupabase Dashboardで先に適用済みであること
（このリポジトリの既存運用として、マイグレーションはこのスクリプト・アプリからは実行しない）。

実行は1回きりの想定（既に同じidentity_idが存在する場合はスキップする、冪等）。
credential値は新規生成したダミー分のみ表示する。config.json由来の既存credentialは
再表示しない。
"""
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

DUMMY_IDENTITY_ID = "dummy_reviewer"


def main() -> None:
    config = load_config()
    client = SupabaseClient(config)

    existing = client.select("geo_ios_identities", params={"select": "identity_id"})
    existing_ids = {row["identity_id"] for row in existing}

    to_insert = []

    for entry in (config.get("geo_ios") or {}).get("identities") or []:
        identity_id = entry.get("identity_id")
        if not identity_id or identity_id in existing_ids:
            continue
        to_insert.append({
            "identity_id": identity_id,
            "credential": entry["credential"],
            "role": entry["role"],
            "domain": entry.get("domain"),
            "created_by": "migration_from_config_2026-09-24",
        })

    dummy_credential = None
    if DUMMY_IDENTITY_ID not in existing_ids:
        dummy_credential = secrets.token_urlsafe(24)
        to_insert.append({
            "identity_id": DUMMY_IDENTITY_ID,
            "credential": dummy_credential,
            "role": "expert_reviewer",
            "domain": None,
            "created_by": "dummy_seed_2026-09-24",
        })

    if not to_insert:
        print("追加なし（全identity_idが既に登録済み）")
        return

    client.insert("geo_ios_identities", to_insert)
    print(f"{len(to_insert)}件登録しました:")
    for row in to_insert:
        if row["identity_id"] == DUMMY_IDENTITY_ID:
            continue
        print(f"  - {row['identity_id']} ({row['role']}) ※config.json由来、credentialは非表示")
    if dummy_credential:
        print(f"  - {DUMMY_IDENTITY_ID} (expert_reviewer) credential: {dummy_credential}")
        print("    ^ この値をTopBar右上の設定(歯車)ダイアログのCredential欄に貼り付けてください")


if __name__ == "__main__":
    main()
