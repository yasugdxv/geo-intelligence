"""Expert Review Comment（読み方カードのタイトル＋レビューコメント）のキャッシュを事前生成する。

使い方（プロジェクト直下で）:
    python scripts/generate_expert_review_cache.py <response_id> [<response_id> ...]

保存済みExpert AI応答（Frozen）は読み取るだけで、書き換え・再生成はしない。Presentation専用の
LLM Transformationを、キャッシュが無い場合だけ実行してexpert_review_cache/へ保存する
（画面を初めて開くより前に済ませておきたい場合・Before/After比較用）。
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import geo_ios_situation_presentation_api as api  # noqa: E402


def main(response_ids):
    for rid in response_ids:
        started = time.time()
        try:
            result = api.generate_expert_review_by_response_id(rid)
            print(json.dumps({"response_id": rid, "status": result["status"],
                              "llm_calls": result.get("llm_calls", 0),
                              "seconds": round(time.time() - started, 1)}, ensure_ascii=False), flush=True)
        except Exception as e:  # noqa: BLE001 — 1件失敗しても残りを続ける
            print(json.dumps({"response_id": rid, "error": f"{type(e).__name__}: {e}"[:300]},
                             ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
