"""会話モードのサーバー保存（geo_intelligence_ai_conversations）の、実DBの確認。

既定は【読み取りのみ】: テーブルと列が存在するかを、select 1回で調べる（書き込みなし）。
    python scripts/geo_conversation_mode_db_check.py

--write-selfcheck --confirm-db-write を付けたときだけ、実DBへ書き込む（SQL適用の承認・適用後に実行すること）:
    テスト用の行（owner=__selfcheck__, id=cm_selfcheck_0001）を保存 → 更新 → 一覧 → 他人からは見えない/消せない →
    削除 → 消えたことを確認、までを行い、途中で失敗しても、その行を削除して終わる。他のデータには触れない。

コンソールにはASCIIだけを出す（Windowsの文字コードで落ちて結果を失わないため）。結果は --out にUTF-8で書く。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TABLE = "geo_intelligence_ai_conversations"
COLUMNS = "conversation_id,owner_id,title,messages,preset,created_at,updated_at"
TEST_OWNER, OTHER_OWNER, TEST_ID = "__selfcheck__", "__selfcheck_other__", "cm_selfcheck_0001"


def check_schema(client) -> dict:
    """テーブルと必要な列があるか（読み取りのみ）。"""
    import geo_intelligence_ai_conversation_store as store
    try:
        client.select(TABLE, {"select": COLUMNS, "limit": "1"})
        return {"table": "exists", "columns": "ok"}
    except Exception as exc:  # noqa: BLE001
        text = str(exc)
        table_gone = any(m in text for m in ("PGRST205", "42P01", "Could not find the table")) or "404" in text
        if not table_gone and "column" in text.lower():          # 列が足りない（テーブルはある）
            return {"table": "exists", "columns": "mismatch", "error": text[:120]}
        if store._missing_table(exc):
            return {"table": "missing", "columns": None}
        return {"table": "unknown", "columns": None, "error": f"{type(exc).__name__}: {text[:120]}"}


def selfcheck_write(client) -> dict:
    """実際に保存・更新・一覧・所有者確認・削除ができるか。テスト行は、成否にかかわらず最後に削除する。"""
    import geo_intelligence_ai_conversation_store as store
    steps: dict = {}
    msgs = [{"role": "user", "content": "selfcheck"}]
    preset = {"kind": "conversation_mode", "mode": "gal_oyaji", "theme": "selfcheck", "state": {"budget": {"llm_calls": 1}}}
    try:
        store.put_conversation(client, TEST_OWNER, TEST_ID, title="selfcheck", messages=msgs, preset=preset)
        steps["put"] = "ok"
        store.put_conversation(client, TEST_OWNER, TEST_ID, title="selfcheck2", messages=msgs * 2, preset=preset)
        mine = [i for i in store.list_conversations(client, TEST_OWNER) if i["id"] == TEST_ID]
        steps["update_keeps_one_row"] = "ok" if len(mine) == 1 and mine[0]["title"] == "selfcheck2" and len(mine[0]["messages"]) == 2 else "FAIL"
        steps["preset_roundtrip"] = "ok" if mine and mine[0]["preset"]["state"]["budget"]["llm_calls"] == 1 else "FAIL"
        steps["other_owner_cannot_see"] = "ok" if not [i for i in store.list_conversations(client, OTHER_OWNER) if i["id"] == TEST_ID] else "FAIL"
        try:
            store.delete_conversation(client, OTHER_OWNER, TEST_ID)
            steps["other_owner_cannot_delete"] = "FAIL"
        except store.ConversationNotFound:
            steps["other_owner_cannot_delete"] = "ok"
        store.delete_conversation(client, TEST_OWNER, TEST_ID)
        gone = not [i for i in store.list_conversations(client, TEST_OWNER) if i["id"] == TEST_ID]
        steps["delete"] = "ok" if gone else "FAIL"
    except Exception as exc:  # noqa: BLE001
        steps["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    finally:
        try:
            client.delete(TABLE, {"conversation_id": f"eq.{TEST_ID}", "owner_id": f"eq.{TEST_OWNER}"})
        except Exception:  # noqa: BLE001
            pass
    leftover = []
    try:
        leftover = client.select(TABLE, {"select": "conversation_id", "conversation_id": f"eq.{TEST_ID}", "limit": "1"})
    except Exception:  # noqa: BLE001
        pass
    steps["test_row_removed"] = "ok" if not leftover else "FAIL"
    steps["all_ok"] = all(v == "ok" for k, v in steps.items() if k != "all_ok")
    return steps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-selfcheck", action="store_true")
    ap.add_argument("--confirm-db-write", action="store_true", help="実DBへ書き込む（テスト行を作って消す）ことを承認済みである")
    ap.add_argument("--out", default="geo_conversation_mode_db_check_result.json")
    args = ap.parse_args()
    if args.write_selfcheck and not args.confirm_db_write:
        print("refused: writing to the real DB is not confirmed (add --confirm-db-write after approval)")
        return 2
    from config_utils import load_config
    from geo_db_client import SupabaseClient
    client = SupabaseClient(load_config())
    report = {"schema": check_schema(client)}
    if args.write_selfcheck:
        if report["schema"].get("table") != "exists" or report["schema"].get("columns") != "ok":
            report["write_selfcheck"] = {"skipped": "table/columns not ready"}
        else:
            report["write_selfcheck"] = selfcheck_write(client)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=True))
    ok = report["schema"].get("table") == "exists" and report["schema"].get("columns") == "ok"
    if args.write_selfcheck:
        ok = ok and bool(report.get("write_selfcheck", {}).get("all_ok"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
