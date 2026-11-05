"""地政学専門家AI v1.2 段階実行(Staged Execution): Strict Schema Contract Check。

Stage1 Schema、および5つのStage2モジュール（audience_signal/review/media_context/
analyst_learning/deep_scenario）それぞれ（と主要な合成パターン）が、実際のAzure OpenAI
デプロイでStrict Structured Output（strict=true）として受理されることを確認する。

jsonschema.Draft7Validator.check_schema によるSchema自体の構文検証（tests/
test_geo_expert_staged_execution.pyで自動テスト済み）だけでは、Azure側の
Strict Structured Output特有の制約（例: 他のdefinitionをネストして参照する定義を
複数箇所から再利用するとSchema全体が無効と判定される、等）を検出できないため、
実APIへの提出可否を別途確認する必要がある。

実行方法:
    python scripts/geo_expert_strict_schema_contract_check.py

Azure OpenAIの認証情報が必要（config.json）。結果は標準出力とdocs/
geopolitical-expert-strict-schema-contract-results.md（手動更新）に記録する。
CI等で自動実行はしない（実APIコール・トークン消費を伴うため）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config_utils import load_config  # noqa: E402
from ai_client import make_openai_client  # noqa: E402
import geo_expert_schema as schema  # noqa: E402
import jsonschema  # noqa: E402

# Phase F(Human Lens / Quality Signal): discretion_assessment/human_interaction_
# coverage/hypothesis_registerはsituation_actor_assessmentモジュールから分離した独立
# モジュールになったため、それぞれ単独の合成パターンを追加する。deep×case_analysisの
# 実際のモジュール構成は`schema.resolve_stage2_modules`から動的に取得し、CHECKS側で
# 手打ちのモジュール名リストが再び実装からずれない（今回のように放置されない）ようにする。
_DEEP_CASE_ANALYSIS_MODULES = tuple(schema.resolve_stage2_modules("case_analysis", "deep"))

CHECKS = [
    # 8000だと稀にcompletion長が超過し応答JSONが途中で切れる(REJECTED、Unterminated
    # string等のparse error)ことを実データで確認したため12000へ引き上げ（Phase Fの
    # フィールド追加によるものではなく、素のStage1でも起き得る出力長のばらつき）。
    ("stage1", lambda: schema.build_stage1_schema(), 12000),
    ("audience_signal", lambda: schema.build_stage2_schema(["audience_signal"]), 8000),
    ("review", lambda: schema.build_stage2_schema(["review"]), 8000),
    ("media_context", lambda: schema.build_stage2_schema(["media_context"]), 8000),
    ("analyst_learning", lambda: schema.build_stage2_schema(["analyst_learning"]), 8000),
    ("deep_scenario", lambda: schema.build_stage2_schema(["deep_scenario"]), 8000),
    ("situation_actor_assessment", lambda: schema.build_stage2_schema(["situation_actor_assessment"]), 8000),
    ("discretion_assessment", lambda: schema.build_stage2_schema(["discretion_assessment"]), 8000),
    ("human_interaction_coverage", lambda: schema.build_stage2_schema(["human_interaction_coverage"]), 8000),
    ("hypothesis_register", lambda: schema.build_stage2_schema(["hypothesis_register"]), 8000),
    ("reasoning_lens_application", lambda: schema.build_stage2_schema(["reasoning_lens_application"]), 8000),
    ("expert_reasoning_questions", lambda: schema.build_stage2_schema(["expert_reasoning_questions"]), 8000),
    ("expert_reasoning_checks", lambda: schema.build_stage2_schema(["expert_reasoning_checks"]), 8000),
    ("combined:audience_signal+media_context+deep_scenario",
     lambda: schema.build_stage2_schema(["audience_signal", "media_context", "deep_scenario"]), 8000),
    ("combined:review+media_context", lambda: schema.build_stage2_schema(["review", "media_context"]), 8000),
    (f"combined:deep_case_analysis({'+'.join(_DEEP_CASE_ANALYSIS_MODULES)})",
     lambda: schema.build_stage2_schema(list(_DEEP_CASE_ANALYSIS_MODULES)), 16000),
    # Phase D-3(Holistic Reasoning Architecture Review): deep×case_analysis/theme_designでは
    # main_scenario生成をCall1(拡張Stage1)からCall2(Scenario専任)へ遅延させる。両方の実際の
    # 呼び出し形状をここでも検証する。deep_scenarioはdefer_scenario=True時はCall1へ含めない
    # （main_scenario/alternative_scenariosはCall2=stage3_scenario側の専任のため）。
    (f"stage1_deferred(+{'+'.join(m for m in _DEEP_CASE_ANALYSIS_MODULES if m != 'deep_scenario')})",
     lambda: schema.build_stage1_schema(
         extra_modules=tuple(m for m in _DEEP_CASE_ANALYSIS_MODULES if m != "deep_scenario"),
         defer_scenario=True),
     # Phase F追加分（discretion_assessment/human_interaction_coverage/interaction_basis）で
     # 必須フィールドが増えた最大のCombined Schema。以前8000で打ち切られ応答JSONが途中で
     # 切れる（REJECTED、Expecting ','等のparse error）不具合を確認したため引き上げる
     # （実運用のStage1 Call1は約14-15k completion tokensを要することを実データで確認済み）。
     20000),
    ("stage3_scenario", lambda: schema.build_stage3_scenario_schema(), 8000),
]


def main():
    cfg = load_config()
    client, model = make_openai_client(cfg)

    results = []
    for name, build_fn, max_tokens in CHECKS:
        s = build_fn()
        jsonschema.Draft7Validator.check_schema(s)
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Test."},
                    {"role": "user", "content": "台湾海峡を巡る架空の緊張激化について、テスト用の分析を返してください。"},
                ],
                response_format={"type": "json_schema",
                                  "json_schema": {"name": "contract_test", "schema": s, "strict": True}},
                max_completion_tokens=max_tokens,
            )
            data = json.loads(resp.choices[0].message.content)
            jsonschema.validate(data, s)
            results.append({"name": name, "accepted": True, "total_tokens": resp.usage.total_tokens})
            print(f"{name}: ACCEPTED (tokens={resp.usage.total_tokens})")
        except Exception as e:
            results.append({"name": name, "accepted": False, "error": str(e)[:300]})
            print(f"{name}: REJECTED - {str(e)[:300]}")

    failed = [r for r in results if not r["accepted"]]
    print()
    print(f"合計 {len(results)} 件中 {len(results) - len(failed)} 件がstrict=trueで受理されました。")
    if failed:
        print("失敗:", [r["name"] for r in failed])
        sys.exit(1)


if __name__ == "__main__":
    main()
