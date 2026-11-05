# READING_EXPOSURE_LAYER_V1 — Freeze Manifest

**Freeze日**: 2026-09-24
**Freeze対象**:
- `geo_ios_reading_exposure_composer.py`（主）
- `tests/test_geo_ios_reading_exposure_composer.py`（主）
- `eval_harness/run_blind_retrospective_diagnostic.py`（Past Case Reproduction Test実行スクリプト）
- `eval_harness/apply_reading_exposure_composer.py`（Reading Exposure Layer適用スクリプト）
- `eval_harness/blind_retrospective_results/*.json`（4 evaluable casesの完全な実行結果——Stage A/B/D生データ、Reading Exposure Before/After全バージョン、Leakage Guard監査結果）

以降、`geo_ios_reading_exposure_composer.py`のSelection Weight・Tie-break定数のチューニングは行わない。新しい要件が生じた場合はV2として別途設計する。

---

## 背景：この機能がなぜ作られたか

Past Case Reproduction / Reading Exposure Validation Test（2026-09-24、5件のEguchi Session/Review由来ケースに対するHard Blind Test）で、以下が判明した。

- Expert AI Step2の`display_top_n=5`は既に実装済みで、問題は表示件数ではなく5件の**構成**だった。
- S（既存のrank付与ロジック、`geo_ios_expert_ai_adapter.py`/`geo_ios_analysis_session_service.py`）は、main_scenario選定においてEvidence Disciplineを保っている（GOOD寄り）。
- `hypothesis_register`のH4（frame-breaking型）がExpert-likeな読みの主な担い手だったが、4ケース中2ケースで`status=rejected`となり、既存Reading Portfolio（`main_scenario`/`alternative_scenarios`しか見ない設計）では救済されなかった。
- Hormuz Tollケースのように、そもそもGold Readingに相当する候補が`hypothesis_register`にも生成されないGENERATION_GAPも存在した（Reading Exposure Layerでは解決できない領域として明示的に切り分けている）。

これを受け、**S/CQAS/Expert AI Coreを一切変更せず**、Selection後段に「どの5件をユーザーへ見せるか」を決める追加レイヤーとして実装したのがReading Exposure Layerである。

---

## 設計原則（V1で確定した責務分離）

### S / Selection
Evidence-supportedなMain Readingを選択する。このレイヤーは一切変更しない——スコア・rank・promptのいずれにも触れない。

### Reading 1-3
S Rank #1-3をそのまま保持する。Exposure Layerは順位・内容を変更しない（再スコアリングしない）。

### Reading 4 / FRONTIER
Top3外の`hypothesis_register`から、本線には採用されなかったが人間が検討する価値のあるReadingを露出する。`retained`/`unresolved`/`rejected`いずれのSteatusも対象にできるが、**Sの判定自体は上書きしない**（rejectedのまま`exploratory=true`で表示し、「AIが有力と判断した」ように見せる表示はしない）。

選定基準: Top3とのsemantic duplicate除外（文字N-gram類似度）、既存構造化フィールドによるEvidence grounding、hypothesis_type（H4を高く扱う既存基準）。

### Reading 5 / EXPLORATORY
Reading Portfolio（`alternative_causal_structure`/`premise_reversal`/`wildcard_non_obvious`）→ 残りの`hypothesis_register` → 必要時のみExpert Exploration新規生成、の優先順で、**Top1-4へ追加することで分析空間が広がるReading**を選択する。

文字列上の距離最大化を主要Selection Signalにすることは禁止（開発中に実際に問題が発生し修正済み——後述）。`semantic`/`structural` diversityを、既存の構造化フィールド（`hypothesis_type`、Reading Portfolioの`slot`、Evidence関連フィールド）から評価する。

---

## 開発中に発見・修正した3つの設計上の問題（v1→v4の変遷）

1. **v1（文字N-gram距離を主要Selection Signalに使用）**: Situationの中心から外れるほど有利になる欠陥。HORMUZ_BLOCKADE_SESSION3で、Evidence groundingの弱いwildcard（地域濃縮スキーム）が、より的確なpremise_reversal（受忍可能な犠牲）より優先される問題が実際に発生した。
2. **v2/v3（hypothesis_typeとReading Portfolio slotを共通のreasoning_familyバケットへ統合）**: taxonomyをまたいだ「既出判定」が、causal mechanismの異なる2つの読みを粗いラベルの一致だけで同一視してしまう問題が判明。Portfolio/hypothesis_registerを別Namespaceで評価する方式へ修正。
3. **v3→v4（hypothesis_register側の"Top4未使用typeなら+0.2"というnovelty bonus）**: hypothesis_registerが通常5件・Top3+Frontierで4件消費される実装の実態上、残る1件がほぼ常に「未使用type」に該当してしまい、ほぼ無条件にhypothesis_register側だけを底上げする系統的バイアスになっていた（4 evaluable cases中3ケースで、Portfolio最良候補に対し0.15pt差の非拮抗な差を生んでいた）。novelty bonusを廃止し、hypothesis_register側は基礎重み＋既存`_type_bonus`のみとした。あわせて、最終スコアが際どい同点（`_NEAR_TIE_EPSILON=0.03`以内）かつ上位2件が異なるnamespaceの場合のみ、Top1-4に無い方のnamespaceへ僅かなbonus（`_NEAR_TIE_NAMESPACE_BONUS=0.01`）を与えるtie-breakを追加した。

**V1としてFreezeするのはこの3回の修正を経た最終状態（v4のロジック）である。**

---

## 4 Validation Casesでの観測：Reading5が全件Reading Portfolio由来になった件について

4 evaluable cases（HORMUZ_TOLL_SESSION1, HORMUZ_BLOCKADE_SESSION3, RUSSIA_SANCTIONS_SESSION2, US_CHINA_AI_SEMICON_SESSION2）では、Reading5が全てReading Portfolio由来（`alternative_causal_structure`スロット）となった。

**これはhard ruleの結果ではなく、現在の候補scoreの結果である。** Frontier（Reading4）が既に`hypothesis_register`の最も尖った候補（多くはH4/frame-breaking型）を先に消費しているため、Reading5の`hypothesis_register`側候補プールには構造的に「相対的に弱い残り物」しか残らない一方、Reading Portfolioの`alternative_causal_structure`/`premise_reversal`スロットは、CQAS本体と重複しないよう設計された専用の追加コンテンツであるため、相対的に優位になりやすい。これは自然な帰結であり、意図的に埋め込んだ順位表ではない。

**4ケースという小サンプルであるため、source namespace比率（例:「Portfolio:hypothesis_register = X:Y にすべき」等）を目的とした追加チューニングは行わない。** ケース数が増えて別の傾向が観測された場合のみ、V2として再検討する。

---

## Freeze条件（すべて確認済み）

| 項目 | 結果 |
|---|---|
| Expert AI Core | unchanged |
| CQAS | unchanged |
| S ranking | unchanged |
| Main Scenario | unchanged（4ケースとも完全一致を確認） |
| Top1-3 | unchanged（4ケースとも完全一致を確認） |
| Reading4 | FRONTIER役割として実装、hypothesis_register由来、Sの判定を上書きしない |
| Reading5 | EXPLORATORY役割として実装、Reading Portfolio/hypothesis_register/(必要時のみ)新規生成から選択 |
| Gold Leakage | 0（5ケース全実行で検出ゼロ、既存の`AsOfEvaluationClient`によるas-of-dateプロキシ経由） |
| unsupported fact | 0（新しいFactを創作しない設計——`expand_hypothesis_to_reading_card`は既存フィールドのみ使用、Expert Exploration生成もsource-grounded anchor必須） |
| 通常経路での追加LLM呼び出し | 0（4 evaluable cases全てで既存Pool内から解決、Expert Exploration新規生成は一度も発火せず） |
| バックエンドregression | **1943 PASS**（新規テスト16件含む、regressionゼロ） |

---

## 関連ファイル

- `geo_ios_reading_exposure_composer.py` — 実装本体
- `tests/test_geo_ios_reading_exposure_composer.py` — 単体テスト（16件）
- `eval_harness/run_blind_retrospective_diagnostic.py` — Past Case Reproduction Test（Hard Blind Input→Expert AI 1回実行→Stage A/B/D記録→Leakage Guard確認）
- `eval_harness/apply_reading_exposure_composer.py` — 保存済みStage A/B/DデータへReading Exposure Layerを適用し、Before/Afterを比較するスクリプト
- `eval_harness/blind_retrospective_results/*.json` — 4 evaluable cases（+ RUBIO_VANCE_ROLE_CHOREOGRAPHY、Source Limited Diagnostic扱い）の完全な実行結果。各ファイルに`reading_exposure_before`、`reading_exposure_after`（現行=v4）、`reading_exposure_after_v1_lexical`/`reading_exposure_after_v2_family_bucket`/`reading_exposure_after_v3_novelty_bonus`（過去バージョンとの比較用）、`leakage_guard`、`as_of_audit_report`を保存済み

## 依存する既存機構（このFreezeの対象外、無変更）

- `geo_expert_engine.py` / `geo_expert_schema.py`（Expert AI Core）
- `geo_ios_expert_ai_adapter.py`（S/rank付与）
- `geo_ios_analysis_session_service.py`（`record_scenario_candidates`、rank/displayed付与）
- `geo_ios_reading_portfolio_service.py`（既存Reading Portfolio、Reading Exposure Layerの入力の一つ）

## 未着手（V1のスコープ外）

- Step2 API/UIへの配線（本Freezeの時点では未実装。UIタグ表示・Human Selection起点のPrimary Reading確定は別タスクとして後続で実施）
- Rubio/Vanceケースの再評価（Blind Source不足のため今回もheadline metricから除外のまま）
- Hormuz Tollケースの構造的GENERATION_GAP（Reading Exposure Layerでは解決不可、CQAS/S側の課題として別途要検討）
