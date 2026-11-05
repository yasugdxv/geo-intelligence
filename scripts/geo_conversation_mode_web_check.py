"""Conversation Mode: Web検索（Perplexity）の動作確認（外部検索を1回だけ送る）。

!! 外部へ検索リクエストを送る。実行には、承認を得たうえで --confirm-one-search が必要。
   --dry-run は、外部へは何も送らず（requests.post をフェイクに差し替え）、この手順書自体の動作だけを確認する。

守っていること:
  - 送るのは固定の公開テーマの題名だけ（ユーザー入力・質問文・資料・社内情報は送らない）。
  - 自動リトライを無効にして、HTTPリクエストは最大1回。
  - フラグ（GEO_CONVERSATION_MODE_*）はこのプロセス内だけで一時的にON。永続的な設定は変えない。
  - 結果は UTF-8 のJSONファイルにだけ書く（コンソールにはASCIIだけを出す。Windowsの文字コードで落ちて結果を失わないため）。
    結果の書き出しは、検索の直後・表示より前に行う。
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

THEME = "ホルムズ海峡の航行リスク"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm-one-search", action="store_true", help="外部検索を1回送ることを承認済みである")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default="geo_conversation_mode_web_check_result.json")
    args = ap.parse_args()
    if not args.dry_run and not args.confirm_one_search:
        print("refused: external search is not confirmed (use --confirm-one-search after approval, or --dry-run)")
        return 2

    os.environ["GEO_CONVERSATION_MODE_ENABLED"] = "true"
    os.environ["GEO_CONVERSATION_MODE_WEB_SEARCH_ENABLED"] = "true"
    import requests

    import geo_conversation_mode_service as svc
    import geo_perplexity_search as pplx

    pplx.MAX_ATTEMPTS = 1
    sent: list = []
    seen: dict = {}
    real_post = requests.post

    class _FakeResp:
        status_code = 200
        text = ""
        headers: dict = {}

        def json(self):
            return {"choices": [{"message": {"content": "要約（ダミー）"}}], "usage": {"total_tokens": 1},
                    "search_results": [{"url": "https://example.com/a", "title": "ダミー", "date": "2026-10-01"}]}

    def spy(url, **kw):
        sent.append({"url": url, "user_content": kw["json"]["messages"][-1]["content"], "model": kw["json"].get("model")})
        r = _FakeResp() if args.dry_run else real_post(url, **kw)
        try:
            seen["usage"] = r.json().get("usage")
        except Exception:  # noqa: BLE001
            seen["usage"] = None
        seen["http"] = r.status_code
        return r

    requests.post = spy
    if args.dry_run and not pplx.get_settings():
        pplx.get_settings = lambda: {"key": "dry-run", "proxies": None, "verify": True}
    t0 = time.monotonic()
    web = svc._run_web(THEME, "recommended")
    report = {"theme": THEME, "dry_run": args.dry_run, "seconds": round(time.monotonic() - t0, 1),
              "http_requests_sent": len(sent), "sent": sent, "http_status": seen.get("http"), "usage": seen.get("usage"),
              "status": web["status"], "reason": web["reason"], "checked_at": web["checked_at"], "items": web["items"]}
    with open(args.out, "w", encoding="utf-8") as f:       # 表示より前に、UTF-8で確実に保存する
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({"saved": args.out, "status": report["status"], "http_requests_sent": report["http_requests_sent"],
                      "http_status": report["http_status"], "items": len(report["items"])}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
