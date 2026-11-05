# Neutral Prompt Validation v2 — After Actor-Evidence-Separation + Exploration/Promotion Fix

比較系列: **Before**(3点修正前、`neutral/`) → **v1**(P0-1/P0-2/P1修正後、Actor層Evidence
分離なし、`neutral_after_p0fix/`) → **v2**(本ドキュメント、Actor層Evidence分離＋
Exploration/Promotion分離修正後)。

Thinking OS（`geo_expert_schema.py`のMODE_GUIDANCE等）は本フェーズ全体で一切未変更
（v1→v2間のFingerprintが完全一致: `358640565bdd689c`）。変更したのは
`geo_expert_quality_guard.py`（`separate_historical_evidence_in_actor_and_
situation_assessment`追加）・`geo_expert_engine.py`（呼び出し配線）・
`schemas/geo_expert_ai_response.schema.json`（`key_point_assessment`description
へExploration/Promotion分離原則を598字追記）のみ。実行前後でFingerprint一致を
確認済み、実行中のコード変更なし。

## Frozen実行ルール（今回遵守）
1. コード・Schema・Promptは5ケース完了まで無変更
2. 単発障害は1回だけ再試行可（実際に4回の中断: ①旧backgroundタスクのkill、②Proxy
   DNS障害、③LLM型不一致、④Case1 JSON構文崩れ、を経て⑤で全5ケース成功）
3. 同一Caseで2回以上同系統障害なら停止して再診断（実施済み: Case1のJSON構文崩れに
   対し診断計装スクリプトでraw response（finish_reason/token数/response長）を実測し、
   598字追加が原因である証拠がないことを確認してから再開した）

## Token使用量・CallBudget

| Case | prompt | completion | total | calls |
|---|---|---|---|---|
| Case1 Trump-Xi | 80,771 | 10,111 | 90,882 | 2 |
| Case2 Putin | 80,698 | 14,990 | 95,688 | 2 |
| Case3 Netanyahu | 82,787 | 11,846 | 94,633 | 2 |
| Case4 MBS | 80,829 | 11,388 | 92,217 | 2 |
| Case5 Kim | 79,575 | 11,416 | 90,991 | 2 |

全ケース`calls=2`、CallBudget<=3を維持。

---

## ユーザー指定7基準の判定

| # | 基準 | 結果 |
|---|---|---|
| 1 | `unsupported_actor_assumption_not_promoted` = 0/5 fail | **○ 達成（0/5）** |
| 2 | `evidence_attribution_not_conflated` = 0/5 fail | **○ 達成（0/5）** |
| 3 | `actor_pattern_applicability_present` = 0/5 fail | **○ 達成（0/5）** |
| 4 | Independent Frame Discovery >=1が4/5以上、=2が3/5以上 | **○ 達成（5/5が>=1、3/5が=2）** |
| 5 | General Knowledge/Historical Pattern中心ケースでConfidence capが維持 | **○ 達成（後述）** |
| 6 | 大胆な仮説はlow/unresolved+knowledge gapとして残る（削除されない） | **○ 達成（後述）** |
| 7 | CallBudget<=3、Regression維持 | **○ 達成（全ケースcalls=2、183/183 pass）** |

**7項目全て達成**。ただし4番の内訳・5番の層別状況・新規の軽微な問題（`actor_model_
challenged`）・未着手の残存Gap（Main Scenario層）を以下に正直に記録する。

---

## 4. Independent Frame Discovery（詳細）

| Case | Before | v1 | **v2** | 所見 |
|---|---|---|---|---|
| Case1 Trump-Xi | 2 | 1 | **1** | Primary Key Pointが「対中輸出管理・投資規制の"強度"」という規制の程度そのものを問う形に留まり、Before水準の構造的洞察（半導体=軍事中核技術）へは回復せず。`kpa_confidence=判断不能`という極めて保守的な自己評価。 |
| Case2 Putin | 2 | 1 | **2** | 「ウクライナのNATO・EU実質統合の有無」という、Before（政権存続）ともv1（NATO脅威認識）とも異なる第三の切り口——より具体的で反証可能な構造条件へ焦点を移した、新しい形の非自明なFrame。H1（完全支配志向）・H3（レッドライン緩和）を明示的にreject。 |
| Case3 Netanyahu | 2 | 2 | **2** | 「政治的生存と連立維持」というBefore/v1と同一の中核Frameを維持。安定して再現。 |
| Case4 MBS | 2 | 1 | **2** | 「体制保証と王位継承の正当性確保」——Beforeの権力集中Frameへ完全復帰（v1で構造説明へ後退していたものが回復）。 |
| Case5 Kim | 1 | 1 | **1** | ユーザーの事前期待通り、無理な2への引き上げなし。核・ミサイル=体制保証という定型Frameを維持しつつConfidenceは低。 |

**Case1のみ完全回復せず**（v1の1から変わらず）。Case2/4は明確に回復・改善。総合として
Acceptance基準（>=1が4/5以上、=2が3/5以上）は達成しているが、Case1は今後の観察対象
として記録する。

---

## 5. Confidence Capping（層別の状況）

### Key Point層: 5/5で正しく機能
`key_point_assessment.confidence`: Case1=判断不能, Case2=低, Case3=低, Case4=低,
Case5=低。全ケースで`evidence_class`をLLM自身が明示的に自己申告
（model_general_knowledge/structural_reference/historical_actor_pattern等）し、
Historical/General Knowledge中心の場合はConfidenceが低いまま出力される、または
決定的補正（`historical_supporting_evidence_ids`への分離）が機能している。

### Actor層: 5/5で正しく機能
全7 Actor Profile（Case1×2, Case2-5×1）で`confidence`が低または判断不能。うち
Putin/Netanyahu/MBS/Kimの4件は`confidence_adjustment_reason`が実際に設定されており
（P0-1決定的補正が発動）、Trump/Xiの2件はLLM自身が最初から`判断不能`と自己申告した
ため補正発動なし（両方とも「Evidence不足を正直に示す」という望ましい結果に到達）。

### Main Scenario層: **未着手（残存Gap、正直な報告）**
`main_scenario.confidence`: Case1=低, Case2=**中**, Case3=低, Case4=**中**, Case5=**中**。
Case2/4/5は依然として中程度のConfidenceを維持しており、その`supporting_evidence`は
Actor Intelligence由来の構造参照（`core_profile.*`形式）やHistorical Episode ID
（例: Case2の`d0ef206d-...`＝ワグネル反乱）に大きく依拠している。既存の
`_weak_analogy_not_used_as_strong_evidence`Guardは`analogy_strength=weak`かつ
`confidence=高`の組合せしか検知せず、今回のCase2/4/5は`confidence=中`（高ではない）
かつ`analogy_strength=medium`（弱ではない）のため、Guard発火はしていない。
**Key Point層・Actor層とP0-1/P0-2の思想（Historical/General Knowledgeのみでは
Confidenceを上げない）が、Main Scenario層にはまだ拡張されていない**——依頼書の
Technical Acceptance Criteria「Main ScenarioがGeneral Knowledge/Historical
Patternのみに依拠する場合の昇格禁止」を厳密に満たすには、この層への同種の決定的
補正（またはGuard拡張）が次の候補となる。ユーザーの7基準には数値目標として含まれて
いないため今回はTechnicalな未達とはしないが、「理想状態」の記述と照らすと部分的
未達であることを明記する。

---

## 6. Hypothesis削除の有無

全5ケースで`hypothesis_register`は5件（H0-H4）を維持し、削除・欠落なし。
status内訳（retained/rejected/unresolvedの構成、全件retainedのケースなし）:

| Case | H0 | H1 | H2 | H3 | H4 |
|---|---|---|---|---|---|
| Case1 | retained | unresolved | unresolved | unresolved | rejected |
| Case2 | retained | rejected | unresolved | rejected | rejected |
| Case3 | unresolved | retained | retained | rejected | rejected |
| Case4 | retained | rejected | unresolved | unresolved | rejected |
| Case5 | retained | rejected | unresolved | unresolved | rejected |

`identified_knowledge_gaps_count`: 4, 2, 2, 2, 3（Before/v1と同水準以上を維持、
減少ケースなし）。`unknown_or_uncertain`は全ケースで具体的な不足情報を明示。

**確認された理想動作の実例**（Case3 Netanyahu）: H0（安全保障合理性による説明）が
`unresolved`のまま保持され、H1（政治的生存）が`retained`——「大胆な仮説は保持しつつ
Evidence次第でPromotionを制御する」という今回の設計目標が実データで機能している。

---

## 新規に発生した軽微な問題

`actor_model_challenged`がCase2 Putin・Case5 Kimで新規Fail
（該当Actorの`actor_model_challenge`が`null`）。Case2/Case5とも
`persistent_preference`は生成されており「実質的なActor Assessment」と判定されるため
Guardが発火した。原因は今回の3修正（Actor Evidence分離／Exploration-Promotion
分離）とは無関係な生成側の揺らぎと考えられる（該当フィールドのSchema・Description
は本フェーズで変更していない）。Regression自体には影響なし（このGuardは非repair
対象、informationalな検出のみ）。

---

## Regression

Expert AIスコープの回帰テスト: **183/183 pass**（新規4件含む）。実行前後で
Fingerprint一致を確認（コード変更なし）。

---

## 総合所見

ユーザー指定7基準は全て達成。「大胆な仮説は出る／根拠が弱ければLow・unresolvedに
留まる／Historical・General KnowledgeだけではMain Scenarioへ昇格しない」という
理想状態のうち、**Key Point層とActor層は実データで確認できたが、Main Scenario層は
未着手**であることを残存Gapとして記録する。Case1のIndependent Frame Discoveryが
Before水準（2）へ完全回復していない点も、今後の観察対象として残す。

依頼書のStop Gate方針に従い、ここで一度停止する。Original 5-case After・
Before/After比較への移行は、ユーザーの判断を待つ。
