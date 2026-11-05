"""Conversation Mode v1 の実LLM E2E（3モード × 同一テーマ）。

使い方（実LLMを呼ぶ。承認を得てから実行すること）:
    set GEO_CONVERSATION_MODE_ENABLED=true
    python scripts/geo_conversation_mode_e2e.py            # 実LLM（上限 MAX_CALLS 回で打ち切る）
    python scripts/geo_conversation_mode_e2e.py --dry-run  # フェイクLLMで、この手順書自体の動作だけを確認（費用なし）

守っていること:
  - LLM呼び出し（analyze内部を含む全ての call_llm_structured）を数え、上限（既定60）に達したら追加実行せず中断して報告する。
  - Web検索は行わない（第2フラグは設定しない）。公開情報だけのテーマ（おすすめ候補と同じ題名）を使う。
  - 匿名（未ログイン）として実行するため、会話テーブルへの書き込みは行わない（DBは読み取りのみ）。
  - 結果（会話の全文・呼び出し数・トークン・所要時間）は、指定した出力ファイルにだけ書く。
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

THEME = "ホルムズ海峡・イラン情勢と海上輸送リスク"
MAX_CALLS = 60


class CallLimitReached(Exception):
    pass


class Anonymous:
    authenticated = False
    identity_id = None
    department = None


class E2EUser:
    """--save-to-db 用のテスト利用者（ログイン済み扱い）。会話は実DBに保存され、最後に全て削除する。"""
    authenticated = True
    identity_id = "__e2e_user__"
    department = None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-calls", type=int, default=MAX_CALLS)
    ap.add_argument("--save-to-db", action="store_true",
                    help="ログイン済みのテスト利用者(__e2e_user__)として、会話を実DBに保存して確認し、最後に全て削除する")
    ap.add_argument("--out", default="geo_conversation_mode_e2e_result.json")
    args = ap.parse_args()

    os.environ["GEO_CONVERSATION_MODE_ENABLED"] = "true"
    os.environ.pop("GEO_CONVERSATION_MODE_WEB_SEARCH_ENABLED", None)

    import geo_conversation_mode_service as svc
    import geo_knowledge_access_policy
    import geo_llm_common

    stats = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "llm_seconds": 0.0}
    real_call = geo_llm_common.call_llm_structured

    if args.dry_run:
        from tests.test_geo_conversation_mode import FakeLLM, fake_search
        from tests._fakes import FakeSupabaseClient
        real_call = FakeLLM()
        svc.search_service.search = fake_search
        client, azure, model = FakeSupabaseClient({"geo_entities": []}), object(), "dry-run"
    else:
        from ai_client import make_openai_client
        from config_utils import load_config
        from geo_db_client import SupabaseClient
        cfg = load_config()
        client = SupabaseClient(cfg)
        azure, model = make_openai_client(cfg)

    def counted(*a, **kw):
        if stats["calls"] >= args.max_calls:
            raise CallLimitReached(f"LLM呼び出しが上限 {args.max_calls} 回に達したため中断")
        stats["calls"] += 1
        t0 = time.monotonic()
        res = real_call(*a, **kw)
        stats["llm_seconds"] += time.monotonic() - t0
        u = (res or {}).get("usage") or {}
        stats["prompt_tokens"] += u.get("prompt_tokens") or 0
        stats["completion_tokens"] += u.get("completion_tokens") or 0
        return res

    geo_llm_common.call_llm_structured = counted
    if args.save_to_db:
        ident = E2EUser()
        ctx = geo_knowledge_access_policy.build_requester_context(user_id=ident.identity_id, department=None, is_authenticated=True)
    else:
        ident = Anonymous()
        ctx = geo_knowledge_access_policy.build_requester_context(user_id=None, department=None, is_authenticated=False)
    created_ids: list = []
    report = {"theme": THEME, "model": model, "dry_run": args.dry_run, "modes": {}, "aborted": None}

    def track(res):
        if res["state"]["conversation_id"] not in created_ids:
            created_ids.append(res["state"]["conversation_id"])

    def step(name, res, mode_report):
        track(res)
        mode_report[name] = {"calls_so_far": stats["calls"], "new": [
            {"speaker": m["speaker"], "basis": m.get("basis"), "refs": m.get("ref_ids"), "ungrounded": m.get("ungrounded"),
             "text": m["content"]} for m in res["new_messages"]]}

    try:
        for mode in ("gal_oyaji", "consult_partner", "stakeholder"):
            mr = report["modes"].setdefault(mode, {})
            t0 = time.monotonic()
            res = svc.start(client, azure, model, identity=ident, requester_context=ctx, mode=mode, theme=THEME,
                            theme_source="recommended")
            step("1_start", res, mr)
            res = svc.turn(client, azure, model, identity=ident, requester_context=ctx, state=res["state"],
                           messages=res["messages"], action="ask", chip="reverse")
            step("2_chip_reverse", res, mr)
            res = svc.turn(client, azure, model, identity=ident, requester_context=ctx, state=res["state"],
                           messages=res["messages"], action="ask", text="もし日本向けの原油輸送が1週間止まったら、最初に何が起きますか？")
            step("3_free_question", res, mr)
            res = svc.turn(client, azure, model, identity=ident, requester_context=ctx, state=res["state"],
                           messages=res["messages"], action="continue")
            step("4_continue", res, mr)
            res = svc.turn(client, azure, model, identity=ident, requester_context=ctx, state=res["state"],
                           messages=res["messages"], action="summarize")
            step("5_summarize", res, mr)
            mr["seconds"] = round(time.monotonic() - t0, 1)
            mr["ledger"] = {k: len(v) for k, v in res["state"]["ledger"].items()}
            ev = res["state"]["evidence"]
            mr["evidence"] = {"query_used": ev.get("query_used"), "search_hits": ev.get("search_hits"), "insufficient": ev.get("insufficient"),
                              "sources": [{"ref_id": x.get("ref_id"), "title": x.get("title")} for x in ev.get("sources") or []],
                              "web": (ev.get("web") or {}).get("status")}
            mr["stakeholders"] = [{"name": p["name"], "grounding": p["grounding"], "db_entity_id": p.get("db_entity_id")}
                                  for p in res["state"].get("stakeholders") or []]
            mr["persistence"] = res["persistence"]
        if args.save_to_db:
            import geo_intelligence_ai_conversation_store as store
            rows = {i["id"]: i for i in store.list_conversations(client, ident.identity_id)}
            report["db"] = {"conversations_saved": len([c for c in created_ids if c in rows]), "conversations_expected": len(created_ids),
                            "stored_budget_matches_state": all(
                                rows[c]["preset"]["state"]["budget"]["llm_calls"] > 0 for c in created_ids if c in rows)}
    except CallLimitReached as exc:
        report["aborted"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        report["aborted"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    finally:
        if args.save_to_db:                                   # テストで作った会話は、成否にかかわらず全て削除する
            import geo_intelligence_ai_conversation_store as store
            for cid in created_ids:
                try:
                    store.delete_conversation(client, ident.identity_id, cid)
                except Exception:  # noqa: BLE001
                    pass
            left = [i for i in store.list_conversations(client, ident.identity_id) if i["id"] in created_ids]
            report.setdefault("db", {})["test_conversations_removed"] = not left
        report["stats"] = {**stats, "llm_seconds": round(stats["llm_seconds"], 1)}
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({"calls": stats["calls"], "prompt_tokens": stats["prompt_tokens"],
                      "completion_tokens": stats["completion_tokens"], "aborted": report["aborted"], "db": report.get("db"),
                      "out": args.out}, ensure_ascii=True))
    return 1 if report["aborted"] else 0


if __name__ == "__main__":
    sys.exit(main())
