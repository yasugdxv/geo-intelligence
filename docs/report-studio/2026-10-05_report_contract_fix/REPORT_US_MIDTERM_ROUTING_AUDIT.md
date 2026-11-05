# REPORT_US_MIDTERM_ROUTING_AUDIT — US Midterm Run の Report Contract / 期間 / Prompt routing 修正

対象Run: `71dd7890-2ba3-4b30-8b57-afc0d5181ce9`（修正前）／修正後の新Run: 今回分 `2f863013-31ed-4d4e-a76f-8c4db7167a20`、過去分 `3983ad3f-3156-4013-bdf2-1ffd31237030`  
作成日: 2026-10-05　状態: **Prompt 1/2は未実行。GO/NO-GO判定待ち（M章・提示物は `preview/` 配下）**

---

## A. Executive Summary

1. **原因はPromptの選択キーが `analysis_mode` だけだったこと。** `public_voice_monitor` を選ぶと、リポジトリに1つしかない Public Voice 用 Prompt 2（v4.3）が常に選ばれる。このv4.3は消費税減税・食品値上げ・S1/S7・2026-05-01 を本文にハードコードした**別案件専用**のPromptだった。US中間選挙用のPrompt 2は**そもそも存在しなかった**。
2. **日付の混同**: マスターの `trend_start_date`（2026-07-01、全17Signal）が、`configured_start_date`・`effective_start_date`・`common_primary_graph_start` へそのまま伝播し、Runの観測期間（9/7〜9/30）が使われていなかった。
3. **primary_graph_signalsが空**: US の Design（v2）に Signal 定義が0件で、自動補完（`continuity_role=Core`）の元が無かった。解決方法も記録されず、「空」の意味が判別できなかった。
4. **Templateは正しかった**（US専用12章が固定されていた）。不整合はPromptだけだった。
5. **修正**: `report_kind` を新設し、Prompt/Templateを `report_kind` で選ぶ登録表（`geo_report_profile_registry.py`）を追加。未登録は `PROMPT_PROFILE_NOT_FOUND` で停止（フォールバックなし）。US専用 Prompt 2 を新規作成。観測期間／履歴の基準日／グラフ開始日を別概念で保存。ZIP生成前に契約検証。
6. **検証**: 回帰テスト 793件通過（新規 契約テスト28件・期間絞り込み8件・保存の衝突2件を含む）。新Run 2件の設定・固定Prompt・Templateは契約検証で エラー0・警告0。手元のPreprocess（DB書き込みなし）で生成した `report_config.yaml` / `monitoring_scope.csv` も契約検証 エラー0・警告0。

---

## B. Current Run Facts（修正前 Run `71dd7890`）

| 項目 | 事実 |
|---|---|
| 期間（Run） | 2026-09-07 〜 2026-09-30（Runに設定済み） |
| 実データ | 全recordが 2026-09-07〜09-30（record map 5ファイル・208,514行、timeline_basis は週開始 9/7〜9/28） |
| Signal | 17（S1〜S17、monitoring_scope.csv で成立） |
| 固定Prompt 2 | 消費税減税・食品値上げ用 v4.3（`消費税`5・`食品`6・`値上げ`6・`2026-05-01`2・`S1 または S7`1・`S1・S7`2・`価格妥当性`1回出現） |
| 固定Template | US専用12章（`sec_02_electoral_position`、`sec_04_candidate_narratives` 等）— 正しい |
| Run config（DB） | `signals: []`、`primary_graph_signals: []`、`common_primary_graph_start: null`、report_kind等なし |
| report_config.yaml | `report_title: public_opinion_report`、`common_primary_graph_start: 2026-07-01`、`primary_graph_signals:`（空） |
| monitoring_scope.csv | 全Signal `configured_start_date=2026-07-01`、`effective_start_date=2026-07-01`（`master_trend_start_date`）、`actual_min_date=2026-09-07` |

## C. Data Period Verification

Raw / Preprocess の record population 自体は 9/7〜9/30 で正常（データ取得期間の問題ではない）。修正後も、新Runと同じ期間指定で手元に生成したPreprocess結果は、期間外 1,462行（9/1〜9/6）を除外して 208,514行・非重複 173,419、実データ範囲 9/7〜9/30（全17Signal）。

## D. Report Type Routing

| 項目 | 修正前 | 修正後 |
|---|---|---|
| Report Typeの保持場所 | なし（`report_title=public_opinion_report` は汎用名） | Design `report_design.report_kind` → Run config `report_kind` |
| Analysis Contract | `public_voice_monitor_v4_3`（Report Typeと混同して使われていた） | 分析方式として別保持（`analysis_contract`） |
| Prompt/Templateの選択キー | `analysis_mode` のみ | `report_kind`（登録表 `PROFILES`） |
| 未登録/未指定 | 共通v4.3へ**暗黙フォールバック** | `PROMPT_PROFILE_NOT_FOUND` で Run作成を停止 |

登録済みprofile: `us_midterm_elections`、`consumption_tax_food_price`、`evidence_intelligence`。EvidenceはモードごとにPromptが専用なので、`report_kind` 未指定でも従来どおり（旧Runは警告で通す）。

## E. Prompt 2 Selection Root Cause

- `pin_run_snapshot` が `ANALYSIS_MODE_PROMPT2_VERSIONS`（`public_voice_monitor → None → 既定 v4.3`）で本文を決めていた。**analysis_contract だけでもなく、analysis_mode だけ**。
- v4.3はタイトル・本文とも消費税/食品値上げ専用（隔週グラフ・S1/S7・5/1開始・2テーマ・12章構成）。US用は存在しなかった。
- stale cache / previous-run contamination: **該当なし**。Promptは Run作成時に固定されるスナップショットで、プロジェクトの「現在値」を再検索していない（`prompt_snapshot` artifact を読む）。Templateも `run.report_template_asset_id`（Run固定）を使っている。誤っていたのはPrompt選択の入力（資源の取り違え）で、cacheの問題ではない。
- **別経路の同種問題（未修正・M章）**: アプリ内AI実行（Application AI の Prompt 1 分類 / Prompt 2 Composition）は、Runに関係なく共通 v4.3 を直接読んでいる。

## F. Date Semantics Root Cause

`build_monitoring_scope_rows` の優先順位が「マスターの設定値 → Runの期間 → 実データ最古日」で、マスターの `trend_start_date`（履歴の基準）が最優先だった。さらに `common_primary_graph_start` はマスターのprimary Signalの開始日の最大値で、Runの期間も実データも考慮されなかった。

修正後は4概念を分離:

| 概念 | 保存先 | US今回分の値 |
|---|---|---|
| Report Type | `report_kind` | `us_midterm_elections` |
| Analysis Contract | `analysis_contract` | `public_voice_monitor_v4_3` |
| Current observation window | `observation_start_date` / `observation_end_date`（Runの期間） | 2026-09-07 〜 2026-09-30 |
| Historical trend baseline | `historical_trend_start_date`（Design） | 2026-07-01 |
| Primary chart start | `primary_chart_start` = `common_primary_graph_start` | **2026-09-07**（= max(履歴基準, 観測開始) をさらにprimary Signalの実データ最古日で制限） |

`monitoring_scope.csv` の `configured_start_date` は従来どおり「マスターの設定値」のまま（意味を変えない）。有効開始日は、観測期間がある場合は「設定開始日と観測開始日の遅い方」。末尾に `observation_start_date` / `observation_end_date` / `historical_trend_start_date` を追加（既存列の名称・順序は不変）。7月・8月のデータがPackageに無い以上、グラフ期間に含めない。

## G. primary_graph_signals Root Cause

US Design v2 の `signals` が 0件、`primary_graph_signals` キーも無し → 自動補完は `continuity_role=='Core'` のSignalだけが対象で、Core が無く空のまま。`validate` は `None` を警告していたが、Run configは `[]` に正規化されて「未設定／全Signal／グラフなし」の区別が付かなかった。

修正: 解決元を `primary_graph_signals_source` に記録（`design` / `design_explicit_empty` / `design_core_autofill` / `narrative_master_trend_role_primary` / `unresolved`）。Designに無い場合は Narrative Master の `trend_role=primary` から解決。`unresolved` のままZIPを作ろうとすると契約検証で止まる。US Design v3 では S1〜S17 を明示（`source=design`）。同様に `signals` も、Designに無ければマスターから導出し `signals_source` を記録。

## H. Template Routing

Run固定の `report_template_asset_id`（US専用12章）が使われていて、ここは正しかった。追加: Run configへ `template_id`（profile）、`template_asset_id`、`template_hash`（内容のsha256）を固定。profileが要求するsection（US: `sec_02_electoral_position`・`sec_04_candidate_narratives`）をTemplateが持たない場合はRun作成時に停止。Prompt 2生成時に「最新テンプレ」を再検索しない構造は従来どおり（Run固定を使用）。

## I. Code Changes

| ファイル | 変更 |
|---|---|
| `geo_report_profile_registry.py`（新規） | `PROFILES`、`resolve_profile`（fail fast）、`lint_prompt_text`、`check_template`、`validate_run_contract`、`content_hash` |
| `resources/report_studio/prompt2/us_midterm_v4.3/instruction.md`（新規） | US中間選挙専用 Prompt 2（下記） |
| `resources/report_studio/prompt1/v4.3/instruction.md` | タイトルの `(v4.2)` → `(v4.3)`（本文のversion行と一致） |
| `geo_report_run_provisioning_service.py` | Profile経由のPrompt選択、config拡張（report_kind・profile・template・日付4概念・signals/primary解決元）、Prompt/Template lint |
| `geo_report_current_pipeline_service.py` | 期間絞り込み（`period_start/end`）、有効開始日の優先順位、グラフ開始日の制限、monitoring_scope追加列、report_config.yaml拡張 |
| `geo_report_current_pipeline_persistence_service.py` | configをartifact生成へ引き渡し、`period_filter.csv`、chunk保存の409を「既に保存済み」として扱う |
| `geo_report_prompt_export_service.py` | ZIP生成前の契約検証（`REPORT_CONTRACT_VALIDATION_ENABLED`）、Run-aware README |
| `geo_report_design_chat_service.py` / `geo_report_project_assets_service.py` | Design警告の判定で `historical_trend_start_date` を認める |
| テスト | 契約テスト26件（`test_geo_report_report_contract.py`）、期間8件、保存衝突2件、既存テストの前提を更新（`conftest.py` で契約検証は既定OFF、検証専用テストだけONに） |

**US中間選挙用 Prompt 2 の方針**: 日付・レース名・Signal数を本文に固定せず、`report_config.yaml` / `monitoring_scope.csv` の値から読む（観測期間・グラフ開始日・対象Signal）。Run固定の `report_template.json` の全章・`required_fields` を基準にする。Candidate Favorability は `targeted_stance_classified.csv`（Public Voice・relevant・apply_to_cluster・candidate・favorable/unfavorable・high/medium・apply_stance_to_cluster）のみから算出し、支持率・投票予測とは呼ばない。Pollデータは入力に無いため、提供時のみ使用し、無い場合は「Poll未提供」と明記して捏造しない。消費税・食品値上げ・S1/S7・2026-05-01等は一切含まない。

## J. Before / After

| 項目 | Before（Run 71dd7890） | After（Run 2f863013、今回分） |
|---|---|---|
| report_kind | なし | `us_midterm_elections` |
| report_title | `public_opinion_report` | `US Midterm Election Monitor` |
| analysis_contract | `public_voice_monitor_v4_3`（yamlのみ・config未記録） | `public_voice_monitor_v4_3` |
| prompt1_profile | なし | `public_voice_monitor_prompt1_v4_3` |
| prompt2_profile | なし（実体は消費税用v4.3） | `us_midterm_elections_prompt2_v4_3` |
| template_id | なし | `us_midterm_state_races`（asset `33432060…`、hash固定） |
| observation_start_date | なし | 2026-09-07 |
| observation_end_date | なし | 2026-09-30 |
| historical_trend_start_date | なし（7/1が混在） | 2026-07-01（別項目） |
| primary_chart_start | 2026-07-01（`common_primary_graph_start`） | **2026-09-07** |
| monitoring_scope: effective_start_date / source | 2026-07-01 / master_trend_start_date | **2026-09-07 / run_period_start**（全17Signal） |
| signal count（Run config） | 0（yamlは17） | 17（`signals_source=narrative_master`） |
| primary_graph_signals | 空 | S1〜S17（`source=design`） |
| Prompt 2 detected topic | 消費税減税・食品値上げ | US中間選挙（Targeted Stance / Candidate Favorability） |
| Prompt 2 detected hardcoded date | `2026-05-01`（2回）、`S1 または S7`・`S1・S7` | なし（0回） |
| Template detected type | US専用12章 | US専用12章（変更なし） |
| Prompt 1 title / version | `(v4.2)` / `v4.3` | `(v4.3)` / `v4.3` |

Targeted Stance contract（Prompt 1 → Prompt 2）: 旧Prompt 2は `targeted_stance_classified.csv` を一切参照せず、新Prompt 2は4回参照（条件も4.4節に明記）。

## K. Regression Tests（793件通過）

| 要求 | テスト |
|---|---|
| A US routing | `test_us_midterm_run_gets_us_prompt2_and_us_profile` |
| B 消費税 routing | `test_consumption_tax_run_gets_consumption_prompt2_not_us` |
| C Missing profile | `test_missing_or_unknown_report_kind_fails_fast_without_default_prompt`（3パターン）、`test_failed_pin_leaves_no_orphaned_run...` |
| D Observation period | `test_observation_window_and_historical_baseline_are_separate_concepts` ほか |
| E Unavailable history | `test_chart_start_never_precedes_observation_window_or_available_data`、`test_chart_start_moves_to_first_data_date...` |
| F Prompt mismatch | `test_us_run_with_consumption_prompt_fails...`、`test_consumption_run_with_us_prompt_fails...`、`test_pin_fails_when_loaded_prompt_belongs_to_another_report_kind`、`test_export_blocks_run_whose_pinned_prompt...` |
| G primary_graph_signals | `test_all_17_races_resolve_primary_graph_signals_explicitly`、`..._resolution_sources`、`test_unresolved_...` |
| その他 | README Run-aware、旧EvidenceRun許容（警告）、旧Public Voice Run拒否、Template section不足の拒否 |

## L. Current Run Recovery Recommendation

| 案 | 内容 | 判断 |
|---|---|---|
| **1. 新Runを作ってPreprocessをやり直す（採用）** | 同じDesign（v3）から新Run。Prompt・config・Templateが新しい契約で固定される | 既存Runの履歴を変えず安全。Preprocessは再実行が必要（今回分 約1.5時間、過去分 約4〜5時間） |
| 2. 既存Runをその場で更新 | Run行・config・Prompt snapshotを書き換える | 不採用。固定済みSnapshotを書き換えるとRunの再現性（Version Isolation）が崩れる。`report_config.yaml` / `monitoring_scope.csv` はPreprocess時のartifactで、書き換えではなく再生成が要る |
| 3. パッケージだけ再生成 | DBのPreprocess結果は流用 | 不可。誤った値は Preprocess artifact に保存済みで、ZIP生成時の再計算では直せない |

**実施済み**: Design v3 公開（report_kind・historical_trend_start_date・primary_graph_signalsを明示）、新Run 2件作成（今回分 `2f863013`、過去分 `3983ad3f`）。**未実施**: 両Runの Preprocess（GO判定後に実行）。DBスキーマ変更・既存Run/レコードの削除は行っていない。

旧Runの扱い: `71dd7890`（旧US今回分）、`a04453c2`（旧US過去分、実行を中断）、`9dd695e4` / `527f239a`（Prompt 2初版を固定）はいずれも `SUPERSEDED`（名前に表示）で、使わない。`docs/report-studio/2026-10-05_prompt_inputs/` の **`US_*` の4つのZIPは消費税用Prompt 2を含み、無効**（使用禁止）。Evidence のZIPは影響なし。

## M. Remaining Risks / GO-NO-GO

**Remaining risks**
1. **Application AI経路が未修正**: `geo_report_prompt1_classification_service` / `geo_report_prompt2_composition_service` が共通v4.3を直接読む。ChatGPT経路（Prompt 1/2 ZIP・統合ZIP）は修正済みだが、アプリ内AIでUS/他案件のPrompt 2 Compositionを動かすと同種の不整合が出る。Run固定のPromptを渡す変更が必要（呼び出し元が多いため今回は対象外）。
2. **消費税プロジェクト（`b228ebd1…`）**: Designに `report_kind` が無いため、次のRun作成は `PROMPT_PROFILE_NOT_FOUND` で止まる（意図どおり）。Designへ `report_kind: consumption_tax_food_price` を追加して公開すれば作成できる（未実施）。
3. **US Prompt 2は新規作成で、ChatGPTでの実運用は未検証**。内容は消費税用v4.3の骨格（結合・分母・QAゲート・出力ZIP）を流用し、US Design・US Templateに合わせた。**Import互換は確認・修正済み**: 取り込みは必須10ファイル名（`biweekly_trend_data.csv` 等）を要求し、レポート編集も `biweekly_trend_data.csv` を読む。初版のUS Prompt 2は別名のCSVを指定していたため、必須10ファイル名に合わせて修正し（US固有の2ファイルは追加）、再発防止テスト（`test_prompt2_instruction_names_every_file_the_import_requires`）を追加した。この修正のためRunを作り直した（先のRun 2件は旧版のPrompt 2を固定していたので `SUPERSEDED`）。
4. **Pollデータが入力に無い**（Template第2章はPollを要求）。Promptは「未提供と明記、捏造しない」としたが、Poll提供の仕組みは未整備。
5. **Prompt 1は全案件共通のまま**（本文に例示の `Arizona` が1行ある）。`targeted_stance` は含まれるため機能上は問題ないが、汎用化は未対応。
6. **「Election-critical / Business-critical州」の分類定義は入力に無い**（Promptは「定義未提供と明記」とした）。
7. 新Runの Preprocess が未実行なので、新Runから作るPrompt 1/2 ZIPの実物は未生成。

**GO / NO-GO 判定材料**（`preview/` に保存）:
`prompt1_instruction.md`、`prompt2_instruction.md`（Run `2f863013` に固定された実物、指紋一致）、`report_config.yaml`、`monitoring_scope.csv`、`README_prompt2_run_context.md`、`preview_summary.json`。

**提案判定: GO WITH WARNINGS** — 契約不一致（Prompt・日付・Signal・primary_graph_signals）は解消し、検証は通っている。次の条件でPreprocessを開始できる: ①US Prompt 2の内容（特に出力ファイル構成）を確認する、②Application AI経路は今回の対象外と了承する。Prompt 1/2の実行は、新RunのPreprocess完了とZIP再生成の後。
