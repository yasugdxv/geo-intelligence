# 地政学専門家AI v1.2 段階実行(Staged Execution): Strict Schema Contract Check結果

`scripts/geo_expert_strict_schema_contract_check.py`を実行し、Stage1 Schema・5つの
Stage2モジュール（audience_signal/review/media_context/analyst_learning/deep_scenario）
単体・主要な合成パターンが、実際のAzure OpenAIデプロイ（`Global-AzureOpenAI-DEV-gpt-5.1`）で
Strict Structured Output（`strict=true`）として受理されることを確認した。

`jsonschema.Draft7Validator.check_schema`によるSchema自体の構文検証（`tests/
test_geo_expert_staged_execution.py`で自動テスト済み）だけでは、Azure側のStrict
Structured Output特有の制約（他のdefinitionをネストして参照する定義を複数箇所から
再利用するとSchema全体が無効と判定される等、実データ検証で発見・`geo_expert_schema._resolve_refs`
で対策済み）を検出できないため、本チェックで実API受理可否を別途確認している。

## 実行結果（2026-08-28）

| Schema | 結果 | 使用トークン |
|---|---|---|
| stage1 | ACCEPTED | 12,780 |
| audience_signal | ACCEPTED | 5,484 |
| review | ACCEPTED | 2,694 |
| media_context | ACCEPTED | 710 |
| analyst_learning | ACCEPTED | 2,048 |
| deep_scenario | ACCEPTED | 4,372 |
| combined: audience_signal+media_context+deep_scenario | ACCEPTED | 8,651 |
| combined: review+media_context | ACCEPTED | 3,034 |

合計8件中8件がstrict=trueで受理された。

## 再実行方法

```
python scripts/geo_expert_strict_schema_contract_check.py
```

Azure OpenAIの認証情報（config.json）が必要。実APIコール・トークン消費を伴うため、CIでの
自動実行は行わない。Stage1/Stage2 Schemaの構造を変更した場合（`geo_expert_schema.py`の
`build_stage1_schema`/`STAGE2_MODULES`/`build_stage2_schema`）は、本チェックを再実行して
Azure側での受理可否を確認すること。
