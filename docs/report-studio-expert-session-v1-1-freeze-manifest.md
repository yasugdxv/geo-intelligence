# Report Studio: Expert Session V1.1 Freeze Manifest

## 1. Freeze名

`REPORT_STUDIO_EXPERT_SESSION_V1_1`

## 2. 前バージョン

`REPORT_STUDIO_EXPERT_SESSION_V1`（commit `ff5bc7a`、manifest commit `9cf7233`）は履歴として保持。本Freezeはこれを置き換える新しいFreeze Pointであり、V1のtag自体は移動・削除しない。

## 3. Root Cause

- Blind Retrospective Diagnosticでの評価は`mode="case_analysis", profile="deep"`を使用していた。
- 一方、Report Studioの本番Reading生成経路（`geo_ios_expert_ai_adapter.generate_scenario_candidates`）は`mode="research_design", profile="standard"`（module-level `DEFAULT_MODE`/`DEFAULT_PROFILE`）を使用していた。
- `research_design`モードは（`profile="deep"`であっても）`hypothesis_register`をStage2モジュールに含まない（`geo_expert_schema.py`のmode×profile表）。
- 結果として、**評価環境で検証していたExpert AIの能力と、本番経路が一致していなかった**。
- 本番ではFrontier Reading生成経路（`hypothesis_register`依存）が構造的に無効化されており、ユーザーがReport Studioで実際に分析セッションを作成すると、常にFrontierが欠落した状態（5件のはずが4件）になっていた。
- これは表示バグではなく、**operationに対するmode/profile選択が誤っていた**ことが根本原因だった。

## 4. V1.1の修正

- `geo_ios_expert_ai_adapter.py`に`READING_GENERATION_MODE = "case_analysis"` / `READING_GENERATION_PROFILE = "deep"`を新設。
- `generate_scenario_candidates`専用の既定値として適用（`_resolve_mode(mode, default=READING_GENERATION_MODE)` + `profile = profile or READING_GENERATION_PROFILE`）。
- module-levelの`DEFAULT_MODE`／`DEFAULT_PROFILE`（`research_design`／`standard`）は無変更。
- `run_consultation_turn`・`refine_design`・Draft生成／AI編集／AIレビュー系など、他5種類の操作は従来どおり共有defaultのまま（`_resolve_mode`は`default`引数を渡さない限り既存動作を維持）。
- フロントエンドから技術的なmode/profileを指定する方式にはしていない——`POST /generate-scenarios`は引き続き`actor_id`/`theme_override`のみを受け取り、Reading生成operationとしての判断は**サーバー側（Adapter関数自体）で一元的に決定**する。
- `onHandoff`（Step1終了時の初回Reading生成）とStep2の再生成ボタンは同一関数を経由するため、**どちらの呼び出し元かではなく「何を生成する処理か」で判定**され、両方とも自動的にcase_analysis/deepの対象になる。
- 実行時のmode/profileを`source_cqas`（`geo_ios_scenario_candidates.content`内の既存jsonb領域）へ記録: `{"mode": "case_analysis", "profile": "deep", "operation": "reading_generation"}`。新規DB migrationなし。

## 5. 非変更対象

以下は今回のFreezeで変更していません:

- Expert AI Coreのmode/profile構成表（`geo_expert_schema.py`のStage2モジュール割当）
- CQAS
- S scoring
- hypothesis taxonomy
- Actor Intelligence
- Relationship Intelligence
- Retrieval（`build_source_pack`等）
- Evidence Binding
- Reading Exposure選定ロジック（`geo_ios_reading_exposure_composer.py`）
- Approval
- Publication
- Learning Candidate
- Gold Caseの内容
- Gold answerのruntime注入（一切行っていない、E2EはBLIND_INPUTのみ使用）

REVIEW MODEのAlternative/Frontier Reading生成（`geo_ios_review_import_service.py`）も無変更——`geo_llm_common.call_llm_structured`直接呼び出しのまま維持（無関係Documentの混入リスクを避けるための明示判断）。

## 6. Test結果

```
新規test: 9 PASS
geo_ios pytest: 395 PASS
TypeScript: 0 error
ESLint: 0 error
```

新規testが保証する内容:

- `generate_scenario_candidates`がmode/profile省略時に`case_analysis/deep`を使う（`test_generate_scenario_candidates_defaults_to_case_analysis_deep`）
- 診断・評価用途等の明示的overrideが引き続き優先される（`test_generate_scenario_candidates_explicit_override_still_wins`）
- 共有`DEFAULT_MODE`/`DEFAULT_PROFILE`が不変（`test_resolve_mode_shared_default_unchanged`）
- Reading生成専用defaultは明示的opt-inでのみ適用される（`test_resolve_mode_reading_generation_default_is_explicit_opt_in`）
- `refine_design`が共有defaultのまま影響を受けない（`test_refine_design_unaffected_by_reading_generation_default`）
- REVIEW MODEがExpert AI Engineを一切呼ばない（`test_geo_ios_review_mode_expert_ai_isolation.py`、4件: モジュールレベルimport非存在、`reconstruct_existing_reading`／`generate_alternative_readings_for_review`のEngine非呼び出し、`generate_directed_exploration_reading`にmode/profileパラメータが存在しないことの静的確認）

## 7. 実経路E2E

本番API経路を使用（`eval_harness/_mode_profile_e2e.py`、リクエストに`mode`/`profile`を含めず、実際のフロントエンド`useGenerateScenarios`と同条件で`POST /generate-scenarios`を実行）。

| Case | hypothesis_register | Step2 UI | Frontier |
|---|---:|---:|---|
| Russia Sanctions | 4 | 5 | 到達 |
| US-China AI / Semiconductor | 4 | 5 | 到達 |
| Hormuz Toll | 5 | 5 | 到達 |
| Hormuz Blockade | 4 | 5 | 到達 |

- 全ケースの`source_cqas`に`{"mode": "case_analysis", "profile": "deep", "operation": "reading_generation"}`が記録されたことを確認。
- 各Case入力は`*_BLIND_INPUT.md`（Gold Case Packの被験者向け入力）のみを使用。Gold文言（`*_EXPERT_GOLD.md`の内容）はruntimeへ一切注入していない。
- 全ケースでMAIN／ALTERNATIVE×2／FRONTIER／EXPLORATORYの5件がStep2 UI相当のAPI応答（`GET /situation-presentation`）まで到達することを確認。

## 8. Goldとの関係

- Hormuz TollのGold R4（「二匹の虎」＝米国内の反イラン勢力と産業界のバランスで撤回を説明する構造的な読み）は、完全一致では再現されていない。
- Hormuz Blockadeの江口氏固有Mechanism（「戦時には損失構造が変わり、海峡を開ける場合と閉じる場合のIranの損得差が縮小する」）も、完全一致では再現されていない。
- 今回の目的はGoldとの完全一致ではなく、**Frontier Reading生成経路を本番で有効にすること**であり、これは4ケース全件で確認済み。
- Goldとの完全一致は今回のRelease Gateにしていない。未再現部分は次回専門家セッション（Eguchi Expert Session）での検証課題として持ち越す。

## 関連ファイル

- `geo_ios_expert_ai_adapter.py`（27行差分）
- `tests/test_geo_ios_phase_c_expert_ai_adapter.py`（既存assertion更新＋新規5テスト）
- `tests/test_geo_ios_review_mode_expert_ai_isolation.py`（新規、REVIEW MODE非変更の保証テスト4件）
