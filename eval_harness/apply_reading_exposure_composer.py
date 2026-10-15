# -*- coding: utf-8 -*-
"""Reading Exposure Layer実装後のBefore/After検証（2026-09-24）。

先に保存済みの5ケース分のPast Case Reproduction Test結果（Stage A/B/D）へ、新規
geo_ios_reading_exposure_composer.compose_reading_exposure()を適用する。Expert AIを
再実行しない——同じ生成結果に対してExposure Layerだけを適用することで、Before(既存の
rank<=5切り出し)とAfter(新Composer)を純粋に比較できる（Expert AI呼び出し自体の
run-to-run variabilityを排除する）。

Reading5が既存Pool（Portfolio/hypothesis_register）だけで埋まらない場合のみ、
1回だけExpert Exploration LLM呼び出しを許可する（Case毎に最大1回）。
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config_utils  # noqa: E402
from ai_client import make_openai_client  # noqa: E402

import geo_ios_reading_exposure_composer as composer  # noqa: E402

RESULTS_DIR = Path(__file__).parent / "blind_retrospective_results"
CASE_IDS = ["HORMUZ_TOLL_SESSION1", "HORMUZ_BLOCKADE_SESSION3", "RUSSIA_SANCTIONS_SESSION2",
            "US_CHINA_AI_SEMICON_SESSION2"]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    cfg = config_utils.load_config()
    azure_client, model = make_openai_client(cfg)

    for case_id in CASE_IDS:
        fp = RESULTS_DIR / f"{case_id}.json"
        with open(fp, encoding="utf-8") as f:
            d = json.load(f)

        before = d["stage_bd_displayed_top5"]
        hypothesis_register = (d["stage_a_full_response_json"] or {}).get("hypothesis_register") or []
        portfolio_candidates = d["stage_c_reading_portfolio_candidates"]

        log(f"=== {case_id}: composing Reading Exposure ===")
        after = composer.compose_reading_exposure(
            ranked_candidates=d["stage_bd_ranked_candidates"],
            hypothesis_register=hypothesis_register,
            portfolio_candidates=portfolio_candidates,
            situation_text=d["query_text"],
            azure_client=azure_client, model=model,
        )
        log(f"  before: {len(before)} readings (raw top5) / after: {len(after)} readings (composed)")
        for r in after:
            log(f"  [{r['reading_role']}] exploratory={r.get('exploratory')} "
                f"status={r.get('original_hypothesis_status')} title={r.get('title')}")

        # Reading5選定ロジック修正の履歴を保全する。
        # v1 = 最初の実装（文字N-gram距離を主要signalに使っていた版）
        # v2 = 1回目修正（taxonomyを跨いだreasoning_familyバケット統合を使っていた版）
        # v3 = 2回目修正（Portfolio/hypothesis_registerを別Namespaceで評価する版、
        #      ただしnovelty bonusが系統的バイアスを生んでいた）
        # v4(現在) = 3回目修正（novelty bonus廃止＋near-tie namespace tie-break追加）
        if "reading_exposure_after" in d and "reading_exposure_after_v1_lexical" not in d:
            d["reading_exposure_after_v1_lexical"] = d["reading_exposure_after"]
        elif "reading_exposure_after" in d and "reading_exposure_after_v2_family_bucket" not in d:
            d["reading_exposure_after_v2_family_bucket"] = d["reading_exposure_after"]
        elif "reading_exposure_after" in d and "reading_exposure_after_v3_novelty_bonus" not in d:
            d["reading_exposure_after_v3_novelty_bonus"] = d["reading_exposure_after"]
        d["reading_exposure_after"] = after
        d["reading_exposure_before"] = before
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2, default=str)
        log(f"  saved updated result to {fp}")


if __name__ == "__main__":
    main()
